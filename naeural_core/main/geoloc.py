import os
import ipaddress
import socket
import struct
import json
import time
from functools import lru_cache
from typing import Optional, Iterable

try:
  import requests
except Exception:
  requests = None

GEOLOC_CACHE_FILENAME = "geoloc_cache.pkl" # Cache filename constant
CACHE_EXPIRATION_SECONDS = 3 * 24 * 60 * 60  # Cache expiration time: 3 days in seconds


class GeoLocator:
  
  def __init__(self, logger):
    self.logger = logger
    self.DC_MAPPINGS = {
      "amazon": "aws",
      "aws": ["aws", "wc2"],
      "google": ("google", "compute engine"),
      "microsoft": "azure",
      "azure": ("microsoft", "hyper-v"),
      "digitalocean": "digitalocean",
      "hetzner": "hetzner",
      "ovh": "ovh",
      "scaleway": "scaleway",
      "hostinger": "hostinger",
      "oracle": ("oracle", "oci"),
      "alibaba": ("alibaba", "aliyun"),
      "linode": "linode",
      "vultr": "vultr",
      "constant": "vultr",
      "tencent": "tencent",
      "openstack": ("openstack",),
      "ovh": ("ovh",),
    }
    return

  # 1) Simple prefixes map (timezone → continent code)
  _TZ_PREFIX_TO_CONTINENT = {
    "Africa": "AF",
    "Antarctica": "AN",
    "Asia": "AS",
    "Europe": "EU",
    "Australia": "OC",  # tz database uses "Australia/..." for AU cities
    "Pacific": "OC",    # Pacific islands -> Oceania
    # "America" is handled specially below
    # The ones below are edge-ish; we try country fallback if given:
    # "Atlantic", "Indian", "Arctic", "Etc"
  }

  # 2) Countries that are in South America (ISO-3166 alpha-2)
  _SOUTH_AMERICA = {
    "AR","BO","BR","CL","CO","EC","FK","GF","GY","PE","PY","SR","UY","VE"
  }

  # 3) Optional helpers for a few tricky Atlantic/Indian cases.
  _AFRICA = {
    "DZ","AO","BJ","BW","BF","BI","CM","CV","CF","TD","KM","CG","CD","CI","DJ",
    "EG","GQ","ER","SZ","ET","GA","GM","GH","GN","GW","KE","LS","LR","LY","MG",
    "MW","ML","MR","MU","YT","MA","MZ","NA","NE","NG","RE","RW","ST","SN","SC",
    "SL","SO","ZA","SS","SD","TZ","TG","TN","UG","EH","ZM","ZW"
  }
  _EUROPE = {
    "AD","AL","AT","AX","BA","BE","BG","BY","CH","CY","CZ","DE","DK","EE","ES",
    "FI","FO","FR","GG","GI","GR","HR","HU","IE","IM","IS","IT","JE","LI","LT",
    "LU","LV","MC","MD","ME","MK","MT","NL","NO","PL","PT","RO","RS","RU","SE",
    "SI","SJ","SK","SM","UA","UK","GB","VA"
  }
  _ASIA = {
    "AE","AF","AM","AZ","BD","BH","BN","BT","CC","CN","CX","GE","HK","ID","IL",
    "IN","IO","IQ","IR","JO","JP","KG","KH","KP","KR","KW","KZ","LA","LB","LK",
    "MM","MN","MO","MV","MY","NP","OM","PH","PK","PS","QA","SA","SG","SY","TH",
    "TJ","TL","TM","TW","UZ","VN","YE"
  }
  _OCEANIA = {
    "AS","AU","CK","FJ","FM","GU","KI","MH","MP","NC","NF","NR","NU","NZ","PF",
    "PG","PN","PW","SB","TK","TO","TV","UM","VU","WF","WS"
  }
  _NORTH_AMERICA = {
    # Core NA
    "BM","CA","GL","MX","PM","US","GS",
    # Central America
    "BZ","CR","SV","GT","HN","NI","PA",
    # Caribbean
    "AG","AI","AW","BB","BL","BQ","BS","CU","CW","DM","DO","GD","GP","HT","JM",
    "KN","KY","LC","MF","MQ","MS","PR","SX","TC","TT","VG","VI"
  }

  def _continent_code_from_tz_and_country(self, timezone: str, country_code: str) -> str:
    """
    Best-effort continent code:
    - Use TZ prefix for clear cases.
    - For 'America/*', split NA vs SA using ISO country.
    - For ambiguous prefixes ('Atlantic','Indian','Arctic','Etc'), fall back to country if available.
    """
    if not timezone:
      # Fall back purely on country if we have it
      return self._continent_from_country(country_code) or "UN"

    prefix = timezone.split("/", 1)[0]

    # Clear/simple prefixes
    if prefix in self._TZ_PREFIX_TO_CONTINENT:
      return self._TZ_PREFIX_TO_CONTINENT[prefix]

    # Split 'America/*' by country
    if prefix == "America":
      if country_code and country_code.upper() in self._SOUTH_AMERICA:
        return "SA"
      return "NA"  # default America → North America

    # Ambiguous prefixes: try country
    if prefix in {"Atlantic", "Indian", "Arctic", "Etc"}:
      cc = (country_code or "").upper()
      if cc:
        return self._continent_from_country(cc) or "UN"

    # Fallback
    return "UN"  # Unknown

  def _continent_from_country(self, cc: Optional[str]) -> Optional[str]:
    if not cc:
      return None
    cc = cc.upper()
    if cc in self._AFRICA: return "AF"
    if cc in self._EUROPE: return "EU"
    if cc in self._ASIA: return "AS"
    if cc in self._OCEANIA: return "OC"
    if cc in self._NORTH_AMERICA: return "NA"
    if cc in self._SOUTH_AMERICA: return "SA"
    if cc in {"AQ","BV","TF"}:  # Antarctica-related territories
      return "AN"
    return None
  
  def P(self, s, *args, **kwargs):
    return self.logger.P(s, *args, **kwargs)

  def _lookup_dc_key(self, info):
    for dc_key, dc_value in self.DC_MAPPINGS.items():
      for val in (dc_value if isinstance(dc_value, (list, tuple)) else [dc_value]):
        if val in info:
          return dc_key
    return None

  def _inet_ntoa_le(self, hex_str: str) -> str:
    """Convert little-endian hex IPv4 (from /proc/net/route) to dotted string."""
    return socket.inet_ntoa(struct.pack("<L", int(hex_str, 16)))


  def _read_text(self, path: str) -> Optional[str]:
    try:
      with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read().strip()
    except Exception:
      return None
        
    
  def _is_public_ip(self, ip: str) -> bool:
    """Return True if IP is valid and public (not private/reserved/etc.)."""
    try:
      obj = ipaddress.ip_address(ip)
    except ValueError:
      return False
    return not (
      obj.is_private
      or obj.is_loopback
      or obj.is_reserved
      or obj.is_link_local
      or obj.is_multicast
    )
    
  @lru_cache(maxsize=128)
  def get_docker_host_ip(self, timeout: float = 0.25) -> Optional[str]:
    """
    Return the Docker host IP reachable from this container.

    The function tries, in order:
    A. Resolve 'host.docker.internal' (supported on Docker Desktop and Docker 20.10+ with host-gateway).
    B. Parse the container's default gateway from /proc/net/route (works on bridge networks; usually 172.17.0.1).
    C. Fall back to the local source address used to reach the Internet (works in --network host).

    Parameters
    ----------
    timeout : float
      DNS resolution timeout in seconds (best-effort).

    Returns
    -------
    Optional[str]
      IPv4 address string, or None if not determinable.

    Notes
    -----
    - On Linux bridge networking, the host is typically reachable at the container's default gateway.
    - On Docker Desktop and newer engines, 'host.docker.internal' often maps to the gateway automatically.
    - In host networking mode, the returned address is the host's outward-facing IP.
    """
    # A) Try special DNS name
    try:
      # Prefer AF_INET
      addrinfo = socket.getaddrinfo("host.docker.internal", None, socket.AF_INET, socket.SOCK_STREAM)
      if addrinfo:
        addr = addrinfo[0][4][0]
        self.P("Container host IP detected via host.docker.internal as: {}".format(addr))
        return addr
    except Exception:
      pass

    # B) Parse default gateway from /proc/net/route
    try:
      with open("/proc/net/route", "r", encoding="utf-8") as f:
        for line in f.read().strip().splitlines()[1:]:
          fields = line.split()
          if len(fields) >= 3:
            dest_hex, gateway_hex, flags_hex = fields[1], fields[2], fields[3]
            # default route
            if dest_hex == "00000000":
              flags = int(flags_hex, 16)
              if flags & 0x2:  # RTF_GATEWAY
                addr = self._inet_ntoa_le(gateway_hex)
                self.P("Container host IP detected via /proc/net/route as: {}".format(addr))
                return addr
    except Exception:
      pass

    # C) Fallback: source address used to reach the Internet (host net or unusual setups)
    addr = None
    try:
      s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
      try:
        s.settimeout(timeout)
        s.connect(("1.1.1.1", 53))
        addr = s.getsockname()[0]
      finally:
        s.close()
    except Exception:
      return None

    self.P("Container host IP detected via fallback '1.1.1.1' as: {}".format(addr))
    return addr

    
  def get_local_ip(self):
    """ Returns the IP of the current host running this script """
    return socket.gethostbyname(socket.gethostname())


  def get_public_ip(
    self, endpoints: Optional[Iterable[str]] = None,
    use_stun: bool = True,
    timeout: float = 3.0
  ) -> Optional[str]:
    """
    Discover the current machine's public (egress) IP.

    Parameters
    ----------
    endpoints : Optional[Iterable[str]]
      HTTP endpoints that return the caller's IP as plain text or JSON.
      If None, a sensible default list is used.
    use_stun : bool, optional
      If True, try STUN as a fallback (requires `pystun3`).
    timeout : float, optional
      Per-request timeout in seconds.

    Returns
    -------
    Optional[str]
      The public IPv4/IPv6 string if discovered, else None.

    Notes
    -----
    - The HTTP method requires outbound internet access and `requests`.
    - STUN fallback can work even if some HTTP endpoints are blocked, but
      still requires outbound UDP and a STUN server reachable from your network.
    - If you're on a VPN/proxy, this will report the VPN/proxy's egress IP.
    """
    eps = list(endpoints or [
      "https://api.ipify.org?format=json",     # {"ip": "..."}
      "https://ifconfig.me/ip",                # plain text
      "https://icanhazip.com",                 # plain text
      "https://ipapi.co/ip",                   # plain text
    ])

    # HTTP attempts
    if requests is not None:
      for url in eps:
        try:
          r = requests.get(url, timeout=timeout)
          if not r.ok:
            continue
          text = r.text.strip()
          self.P(f"Found response from {url}: {text}")
          # Handle JSON shape from api.ipify.org
          if text.startswith("{"):
            data = r.json()
            cand = (data.get("ip") or "").strip()
          else:
            cand = text
          if self._is_public_ip(cand):
            return cand
        except Exception:
          continue

    # STUN fallback (IPv4)
    if use_stun:
      try:
        # pip install pystun3
        import stun
        self.P("Using stun to detect public IP...")
        nat_type, external_ip, external_port = stun.get_ip_info(
          stun_host="stun.l.google.com",
          stun_port=19302,
          source_port=0
        )
        if external_ip and self._is_public_ip(external_ip):
          return external_ip
      except Exception:
        pass

    return None


  def _local_mmdb_country(
    self, ip: str, mmdb_path: Optional[str], return_iso: bool
  ) -> Optional[str]:
    """Try to resolve via a local MaxMind GeoLite2-Country database."""
    try:
      import geoip2.database
    except Exception:
      return None
    path = mmdb_path or os.getenv("GEOLITE2_COUNTRY_DB", "./GeoLite2-Country.mmdb")
    if not os.path.exists(path):
      return None
    try:
      with geoip2.database.Reader(path) as reader:
        resp = reader.country(ip)
        return resp.country.iso_code if return_iso else resp.country.name
    except Exception:
      return None
    

  def _parse_ipapi_location_and_datacenter(self, data):
    city = data.get("city")
    country_name = data.get("country_name")
    country_code = data.get("country_code")
    country_code_iso3 = data.get("country_code_iso3")
    continent_code = data.get("continent_code")
    org = data.get("org")    
    
    result = {
      "ip": data.get("ip"),
      "country": country_name,
      "country_code": country_code,
      "country_code_iso3": country_code_iso3,
      "city": city,
      "continent": continent_code,
      "datacenter": self._lookup_dc_key(org.lower() if org else "")
    }
    return result    
  
  
  def _parse_ipinfo_location_and_datacenter(self, data):
    """
      {
        "ip": "31.97.123.191",
        "city": "Frankfurt am Main",
        "region": "Hesse",
        "country": "DE",
        "loc": "50.1155,8.6842",
        "org": "AS47583 Hostinger International Limited",
        "postal": "60306",
        "timezone": "Europe/Berlin",
        "readme": "https://ipinfo.io/missingauth"
      }    
    """
    country_name = data.get("country")
    country_code_iso = data.get("country")
    city = data.get("city")
    tz = data.get("timezone")
    org = data.get("org")

    continent_code = self._continent_code_from_tz_and_country(tz, country_code_iso)

    result = {
      "ip": data.get("ip"),
      "country": country_name,
      "country_code": country_code_iso,
      "city": city,
      "continent": continent_code,
      "datacenter": self._lookup_dc_key(org.lower() if org else "")
    }
    return result

  def _load_from_cache(self, ip: str) -> Optional[dict]:
    """Load geolocation data from cache if it exists and is not expired."""
    try:
      cache_data = self.logger.load_pickle_from_data(GEOLOC_CACHE_FILENAME, verbose=False)
      if cache_data and isinstance(cache_data, dict):
        # Check if we have data for this IP
        if ip in cache_data:
          entry = cache_data[ip]
          if isinstance(entry, dict) and 'data' in entry and 'timestamp' in entry:
            # Check if cache entry is not expired
            current_time = time.time()
            if current_time - entry['timestamp'] < CACHE_EXPIRATION_SECONDS:
              return entry['data']
            else:
              # Remove expired entry
              del cache_data[ip]
              self.logger.save_pickle_to_data(cache_data, GEOLOC_CACHE_FILENAME, verbose=False)
    except Exception as exc:
      self.P(f"Error loading from cache: {exc}")
    return None

  def _save_to_cache(self, ip: str, data: dict) -> None:
    """Save geolocation data to cache."""
    try:
      # Load existing cache or create new one
      cache_data = {}
      try:
        cache_data = self.logger.load_pickle_from_data(GEOLOC_CACHE_FILENAME, verbose=False)
        if cache_data is None:
          cache_data = {}
      except:
        cache_data = {}
      
      # Add new entry with timestamp
      cache_data[ip] = {
        'data': data,
        'timestamp': time.time()
      }
      
      # Save updated cache
      self.logger.save_pickle_to_data(cache_data, GEOLOC_CACHE_FILENAME, verbose=False)
      self.P(f"Cached geolocation data for IP: {ip}")
    except Exception as exc:
      self.P(f"Error saving to cache: {exc}")

  def _http_country(self, ip: str, skip_ipapi=False) -> dict:
    """Try public APIs (ipapi.co first; then ipinfo.io if token provided)."""
    if requests is None:
      return None
    
    # Check cache first
    cached_data = self._load_from_cache(ip)
    if cached_data is not None:
      self.P(f"Using cached geolocation data for IP: {ip}")
      return cached_data
    
    url1 = f"https://ipapi.co/{ip}/json/"
    r = None
    if not skip_ipapi:
      try:
        self.P("Fetching location data from {}".format(url1))
        r = requests.get(url1, timeout=3)
        if r.status_code == 200:
          data = r.json()
          info = self._parse_ipapi_location_and_datacenter(data)
          self.P("Detected location {} ({}, {}, {}), Datacenter: {}".format(
            info.get("country"), info.get("country_code"), info.get("city"),
            info.get("continent"), info.get("datacenter")
          ))
          # Save to cache
          self._save_to_cache(ip, info)
          return info
        else:
          self.P("{} failed with code {} and data: {}".format(
            url1, r.status_code, r.text
          ))
      except Exception as exc:
        self.P(f"Error fetching {url1}: {exc}")


    try:
      url2 = f"https://ipinfo.io/{ip}/json"
      token = os.getenv("IPINFO_TOKEN")
      if token:
        self.P(f"Fetching location data from {url2} with token...")
        url2 += f"?token={token}"
      else:
        self.P(f"Fetching location data from {url2} without token...")
      r = requests.get(url2, timeout=3)
      if r.ok:
        data = r.json()
        info = self._parse_ipinfo_location_and_datacenter(data)
        self.P("Detected location {} ({}, {}, {}), Datacenter: {}".format(
          info.get("country"), info.get("country_code"), info.get("city"),
          info.get("continent"), info.get("datacenter")
        ))
        # Save to cache
        self._save_to_cache(ip, info)
        return info
    except Exception as exc:
      self.P(f"Error fetching {url2}: {exc}")
    return None


  @lru_cache(maxsize=4096)
  def geolocate_ip(
    self, ip: Optional[str] = None, *,
    prefer_local: bool = True,
    return_iso: bool = False,
    mmdb_path: Optional[str] = None,
    discover_when_private: bool = True,
    skip_ipapi: bool = False,
  ) -> Optional[str]:
    """
    Determine the country for an IP address. If the IP is private/LAN, optionally
    discover the public egress IP and geolocate that instead.

    Parameters
    ----------
    ip : Optional[str], optional
      IP address (IPv4/IPv6). If None, the function will attempt to discover
      the public IP and geolocate it.
    prefer_local : bool, optional
      If True, try a local GeoLite2-Country database first. Otherwise use HTTP first.
    return_iso : bool, optional
      If True, return ISO 3166-1 alpha-2 code (e.g., 'US'); else country name.
    mmdb_path : Optional[str], optional
      Path to GeoLite2-Country.mmdb (or via $GEOLITE2_COUNTRY_DB).
    discover_when_private : bool, optional
      If True and `ip` is private/non-public, attempt to discover public IP
      and geolocate that.

    Returns
    -------
    Optional[str]
      Country name (or ISO code), or None if unknown/unavailable.

    Examples
    --------
    >>> geolocate_ip("192.168.1.10", return_iso=True)  # LAN IP, discover egress
    'RO'
    >>> geolocate_ip()  # auto-discover public IP
    'United States'
    """
    target_ip = ip
    
    local_ip = self.get_local_ip()
    self.P("Local IP detected as: {}".format(local_ip))
    docker_host = self.get_docker_host_ip()
    self.P("Container host IP detected as: {}".format(docker_host))

    if not target_ip:      
      target_ip = self.get_public_ip()
      if not target_ip:
        return None

      self.P("Public IP detected as: {}".format(target_ip))

    if not self._is_public_ip(target_ip):
      if discover_when_private:
        pub = self.get_public_ip()
        if not pub:
          return None
        target_ip = pub
      else:
        return None

    self.P("Geolocating IP: {}".format(target_ip))
    res = None
    if False:
      backends = (
        (self._local_mmdb_country, self._http_country)
        if prefer_local else
        (self._http_country, self._local_mmdb_country)
      )
      for fn in backends:
        if fn is self._local_mmdb_country:
          res = fn(ip=target_ip, mmdb_path=mmdb_path, return_iso=return_iso)
        else:
          res = fn(ip=target_ip, return_iso=return_iso)
        if res:
          break
    else:
      res = self._http_country(
        ip=target_ip, skip_ipapi=skip_ipapi
      )
    # endif
    if isinstance(res, dict):
      res['local_ip'] = local_ip
      res['docker_host_ip'] = docker_host
    return res

  
  @lru_cache(maxsize=64)
  def check_if_host_is_datacenter(self, timeout: float = 0.35, try_asn_fallback: bool = True) -> Optional[str]:
    """
    Detect the cloud/VPS provider for the current host. Returns a string like
    'aws', 'azure', 'gcp', 'digitalocean', 'hetzner', 'linode', 'vultr', 'upcloud', 'hostinger', etc.
    """
    # --- A) Metadata probes ---
    if requests:
      try:
        # AWS (IMDSv2 then v1)
        try:
          t = requests.put(
            "http://169.254.169.254/latest/api/token",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
            timeout=timeout
          )
          if t.ok and t.text:
            r = requests.get(
              "http://169.254.169.254/latest/meta-data/instance-id",
              headers={"X-aws-ec2-metadata-token": t.text},
              timeout=timeout
            )
            if r.ok:
              return "aws"
        except Exception:
          pass
        try:
          r = requests.get("http://169.254.169.254/latest/meta-data/", timeout=timeout)
          if r.ok:
            return "aws"
        except Exception:
          pass

        # Azure
        try:
          r = requests.get(
            "http://169.254.169.254/metadata/instance?api-version=2021-02-01",
            headers={"Metadata": "true"},
            timeout=timeout
          )
          if r.ok:
            return "azure"
        except Exception:
          pass

        # GCP
        try:
          r = requests.get(
            "http://169.254.169.254/computeMetadata/v1/project/project-id",
            headers={"Metadata-Flavor": "Google"},
            timeout=timeout
          )
          if r.ok and r.headers.get("Metadata-Flavor", "").lower() == "google":
            return "gcp"
        except Exception:
          pass

        # DigitalOcean
        try:
          r = requests.get("http://169.254.169.254/metadata/v1.json", timeout=timeout)
          if r.ok and "droplet" in r.text.lower():
            return "digitalocean"
        except Exception:
          pass

        # Hetzner Cloud
        try:
          r = requests.get("http://169.254.169.254/hetzner/v1/metadata", timeout=timeout)
          if r.ok:
            return "hetzner"
        except Exception:
          pass

        # Oracle OCI
        for url in ("http://169.254.169.254/opc/v2/instance/", "http://169.254.169.254/opc/v1/instance/"):
          try:
            r = requests.get(url, timeout=timeout)
            if r.ok:
              return "oci"
          except Exception:
            pass

        # Alibaba Cloud (Aliyun)
        try:
          r = requests.get("http://100.100.100.200/latest/meta-data/instance-id", timeout=timeout)
          if r.ok:
            return "alibaba"
        except Exception:
          pass

        # OpenStack (generic)
        try:
          r = requests.get("http://169.254.169.254/openstack/latest/meta_data.json", timeout=timeout)
          if r.ok:
            return "openstack"
        except Exception:
          pass
      except Exception:
        pass

    # --- B) DMI/BIOS heuristics ---
    dmi = " ".join(filter(None, [
      self._read_text("/sys/class/dmi/id/sys_vendor"),
      self._read_text("/sys/class/dmi/id/product_name"),
      self._read_text("/sys/class/dmi/id/bios_vendor"),
      self._read_text("/sys/class/dmi/id/bios_version"),
    ])).lower()

    if dmi:
      dc_key = self._lookup_dc_key(dmi)
      if dc_key:
        return dc_key

    # --- Linode detection via DNS trick ---
    if requests:
      try:
        ip = self.get_public_ip()
        if ip:
          dashed = ip.replace('.', '-')
          try:
            resolved = socket.gethostbyname(f"{dashed}.ip.linodeusercontent.com")
          except Exception:
            resolved = None
          if resolved == ip:
            return "linode"
      except Exception:
        pass

    # --- C) ASN/org fallback (if enabled) ---
    if try_asn_fallback and requests:
      try:
        # Get public IP (again if needed)
        ip = None
        for ep in ("https://ipapi.co/ip", "https://ifconfig.me/ip", "https://icanhazip.com"):
          try:
            r = requests.get(ep, timeout=timeout)
            if r.ok:
              ip = r.text.strip()
              break
          except Exception:
            continue

        if ip:
          r = requests.get(f"https://ipapi.co/{ip}/json/", timeout=timeout)
          if r.ok:
            data = r.json()
            org = (data.get("org") or data.get("asn") or "").lower()
            dc_key = self._lookup_dc_key(org)
            if dc_key:
              return dc_key
      except Exception:
        pass

    return None


  # Alias to preserve old method name
  def is_host_in_datacenter(self, timeout: float = 0.35, try_asn_fallback: bool = True) -> Optional[str]:
    return self.check_if_host_is_datacenter(timeout=timeout, try_asn_fallback=try_asn_fallback)


  def get_location_and_datacenter(self, skip_ipapi=False):
    data = self.geolocate_ip(skip_ipapi=skip_ipapi)
    return data



if __name__ == "__main__":
  try:
    from ratio1 import Logger
    l = Logger("IPTEST", base_folder=".", app_folder="_local_cache")
  except:
    class Logger:
      def P(self, msg):
        print(f"[{self.name}] {msg}")
    l = Logger()

  eng = GeoLocator(logger=l)
  
  l.P("=== Testing geolocate_ip() ===")
  loc_and_dc = eng.get_location_and_datacenter()
  l.P(json.dumps(loc_and_dc, indent=2))
