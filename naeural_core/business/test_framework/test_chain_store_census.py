"""
Offline tests of the chain store's hourly CSTORE_CENSUS line: per namespace bucket, the records, unstamped records,
tombstones, readonly records, size and a digest that two replicas holding the same records share.
"""
# The census line's JSON.
import json
# The test framework.
import unittest
# The block engine and NetMon stand-ins of process().
from types import SimpleNamespace

# The composer of a hash key.
from test_chain_store_hsync import _hset_key
# The order tests' store builder, record builder and constants.
from test_chain_store_order import NOW, NOW_MS, make, record

# The census step, by its private name.
CENSUS = "_ChainStoreBasePlugin__log_census"
# A field of the namespace "players", and its bucket.
ALPHA, BETA = _hset_key("players", "alpha"), _hset_key("players", "beta")
# The bucket of "players".
PLAYERS = ALPHA[:14]


def census(store):
  """
  Run the census step of ``store`` and give its line's rows.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    The replica.

  Returns
  -------
  tuple
    ``(rows, line)``: the parsed rows (bucket -> row) and the whole log line.

  Examples
  --------
  >>> census(make("report"))[0]
  {}
  """
  # The census step.
  getattr(store, CENSUS)()
  # Its line, the last message.
  line = store.messages[-1]
  # The rows, and the line as written.
  return json.loads(line[len("CSTORE_CENSUS "):]), line


class TestCensus(unittest.TestCase):
  """The census line's rows, its digest, what it never shows, and when it is written."""

  def test_rows_count_records_unstamped_tombstones_readonly_and_size(self):
    """A bucket's row counts its live records, the unstamped ones, the tombstones of its keys and the readonly ones,
    and sums the characters of the values' canonical JSON; plain keys share one bucket; a key under hs: that is not
    shaped like a namespace prefix is left out."""
    # A stamped record, an unstamped readonly one, a tombstone of a third field, a plain record, and a reserved key.
    store = make("report", storage={
      ALPHA: record(ALPHA, {"b": 1, "a": 2}, "peer1", [NOW_MS, "peer1"]),
      BETA: {**record(BETA, "v", "peer1"), "readonly": True},
      "dl:" + _hset_key("players", "gone"): record(_hset_key("players", "gone"), None, "peer1", [NOW_MS, "peer1"]),
      "plain-key": record("plain-key", 7, "peer2", [NOW_MS, "peer2"]),
      "hs:cstore_pull": record("hs:cstore_pull", {"floor": 1}, "node"),
    })
    # The rows, without their digests.
    rows = {bucket: row[:5] for bucket, row in census(store)[0].items()}
    # Two live records (one unstamped and readonly) and a tombstone in "players"; one record in "plain".
    self.assertEqual(rows, {PLAYERS: [2, 1, 1, 1, len('{"a": 2, "b": 1}') + len('"v"')], "plain": [1, 0, 0, 0, 1]})

  def test_the_digest_depends_on_the_records_not_on_their_order_or_bookkeeping(self):
    """Two replicas holding the same records, inserted in opposite orders, with a value's dict keys in another order
    and other confirmation fields, owners and a raised stamp, give the same line; a changed value changes only its
    own bucket's digest."""
    # The records: three fields of "players" and one plain key.
    records = [record(ALPHA, {"x": 1, "y": [1, 2]}, "peer1", [NOW_MS, "peer1"]), record(BETA, "b", "peer2"),
               record(_hset_key("players", "gamma"), 3.5, "peer1", [NOW_MS + 1, "peer1"]), record("plain", "p", "x")]
    # One replica with them in this order.
    first = make("report", "n1", storage={rec["key"]: rec for rec in records})
    # The other in the opposite order, ALPHA's dict keys reversed, other bookkeeping fields and owners.
    second = make("report", "n2", storage={rec["key"]: {**rec, "confirms": 3, "owner": "other",
                                                         "target_peers": ["a"], "confirmed_peers": ["a"]}
                                           for rec in reversed(records)})
    # ALPHA's value with its keys in the other order, and its stamp raised by an identical write elsewhere.
    second.storage[ALPHA]["value"], second.storage[ALPHA]["ver"] = {"y": [1, 2], "x": 1}, [NOW_MS + 5, "peer9"]
    # The same line.
    self.assertEqual(census(first)[1], census(second)[1])
    # One value changed on the second replica.
    second.storage[BETA] = record(BETA, "c", "peer2")
    # The two lines' rows.
    one, two = census(first)[0], census(second)[0]
    # Only the "players" digest differs.
    self.assertEqual((one[PLAYERS][5] != two[PLAYERS][5], one["plain"], one[PLAYERS][:5]),
                     (True, two["plain"], two[PLAYERS][:5]))

  def test_the_line_shows_no_key_and_no_value_and_hashes_no_secret_or_token_value(self):
    """The line holds no key, field name or value; the digest of a dAuth secret record and of a record with a token
    does not change with the value (it hashes the key and the stamp), while that of an ordinary record does."""
    # A dAuth secret, a record with a token, and an ordinary record, each with a recognisable value.
    secret, ordinary = _hset_key("DAUTH_JOB_SECRETS", "7"), _hset_key("players", "visible-field")
    # The replica.
    store = make("report", storage={
      secret: record(secret, {"job_secrets": {"TOKEN": "super-secret"}}, "oracle", [NOW_MS, "oracle"]),
      "tok-key": {**record("tok-key", "token-guarded", "peer1", [NOW_MS, "peer1"]), "token": "t"},
      ordinary: record(ordinary, "plain-visible", "peer1", [NOW_MS, "peer1"]),
    })
    # The line.
    rows, line = census(store)
    # None of the keys, decoded field names or values appears in it.
    self.assertEqual([text for text in (secret, "tok-key", ordinary, "visible-field", "super-secret", "token-guarded",
                                        "plain-visible", "TOKEN") if text in line], [])
    # Every value changed, the stamps kept.
    for key, value in ((secret, {"job_secrets": {"TOKEN": "other"}}), ("tok-key", "other"), (ordinary, "other")):
      # The new value.
      store.storage[key]["value"] = value
    # The new rows.
    again = census(store)[0]
    # The secret's and the token record's digests kept; the ordinary record's changed.
    self.assertEqual([rows[bucket][5] == again[bucket][5] for bucket in (secret[:14], "plain", ordinary[:14])],
                     [True, True, False])

  def test_the_census_follows_the_first_stats_line_and_every_twelfth(self):
    """Over 25 CSTORE_STATS lines the census line follows the 1st, the 13th and the 25th, each right after it."""
    # A replica with one record.
    store = make("report", storage={ALPHA: record(ALPHA, "v", "peer1", [NOW_MS, "peer1"])})
    # Its whitelist and NetMon for the peer refresh of process().
    store.bc = SimpleNamespace(get_whitelist=lambda with_prefix=False: ["peer1", "peer2"])
    # Both peers running.
    store.netmon = SimpleNamespace(network_node_simple_status=lambda peer, **kwargs: "ONLINE")
    # The interval of the stats line.
    interval = store.CS_STATS_INTERVAL
    # 25 intervals, each ended by a lap.
    for n in range(1, 26):
      # The interval ends.
      store._now = NOW + n * interval
      # The lap that writes the line.
      store.process()
    # The kinds of line in order, numbered by stats line.
    kinds = [msg.split(" ", 1)[0] for msg in store.messages if msg.startswith(("CSTORE_STATS", "CSTORE_CENSUS"))]
    # The stats lines each census line follows.
    self.assertEqual([kinds[:index].count("CSTORE_STATS") for index, kind in enumerate(kinds)
                      if kind == "CSTORE_CENSUS"], [1, 13, 25])


if __name__ == "__main__":
  # Run this module alone.
  unittest.main()
