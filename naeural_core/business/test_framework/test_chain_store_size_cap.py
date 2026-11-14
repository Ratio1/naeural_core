"""
Offline tests of the chain store's record size cap, of the refusal of a record no message can carry, and of the
count of new keys per sender outside the replication peers and the oracle registries.
"""
# A datetime value, which the wire's encoder turns into text.
import datetime
# The independent measure of a record's wire form, and the wire form of a sync answer.
import json
# The test framework.
import unittest

# A numpy value, which the wire's encoder turns into a plain number.
import numpy as np
# The encoder a node uses on the wire (the plugin's json_dumps).
from ratio1.logging.logger_mixins.json_serialization_mixin import _JSONSerializationMixin

# The counter attribute's private name.
from test_chain_store_accounting import STATS
# The composer of a hash key.
from test_chain_store_hsync import _hset_key
# The order tests' store builder, deliverer of a live store, record builder, constants and private names.
from test_chain_store_order import BUCKET, CLOCK, EXPORT, KEY, MERGE, NOW_MS, answers, deliver, make, record

# The cap, in bytes of the record's wire form.
CAP = 262_144
# A second field of the namespace "players".
OTHER = _hset_key("players", "beta")
# The send queue of local writes, by its private name.
OPS = "_ChainStoreBasePlugin__ops"
# A string UTF-8 cannot encode: a lone surrogate, as the JSON escape "\ud800" in a peer's message decodes to.
SURROGATE = "bad \ud800 text"


def node(mode, **kwargs):
  """
  Build a chain store that encodes as a node does: the store of the order tests with the wire's encoder.

  Parameters
  ----------
  mode : str
    ``'off'``, ``'report'`` or ``'on'``, for every switch.
  **kwargs : dict
    The other arguments of the order tests' ``make`` (``address``, ``peers``, ``storage``, ``signed``).

  Returns
  -------
  _ChainStoreRuntimeHarness
    The store; its ``json_dumps`` is the logger's ``safe_json_dumps``, the plugin's encoder (compact separators,
    numpy and datetime values taken), instead of the harness's plain ``json.dumps``.

  Examples
  --------
  >>> node("report").json_dumps({"a": 1}, sort_keys=False)
  '{"a":1}'
  """
  # The store of the order tests.
  store = make(mode, **kwargs)
  # The encoder every message leaves a node with.
  store.json_dumps = _JSONSerializationMixin.safe_json_dumps
  # Ready.
  return store


def wire_size(key, value, owner, readonly=False, token=None, ver=None):
  """
  Measure a record as the cap does, without the store: the UTF-8 bytes of its entry in a sync answer plus its token.

  Parameters
  ----------
  key : str
    Composed key.
  value : any
    The value (plain JSON types).
  owner : any
    The owner.
  readonly : any, optional
    The readonly mark; False by default.
  token : any, optional
    The token; None by default.
  ver : list, optional
    The stamp; None (the default) for an unstamped record.

  Returns
  -------
  int
    The bytes of ``{key: {key, value, owner, readonly, token, ver}}`` as compact JSON with non-ASCII characters as
    themselves, encoded as UTF-8.

  Examples
  --------
  >>> wire_size("k", "v", "o")   # {"k":{"key":"k","value":"v","owner":"o","readonly":false,"token":null,"ver":null}}
  82
  """
  # Compact JSON, as the wire's encoder writes it, then the bytes that travel.
  return len(json.dumps({key: {"key": key, "value": value, "owner": owner, "readonly": readonly, "token": token,
                               "ver": ver}}, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def filler(size, key, owner, ver=None):
  """
  Give the string value that makes a record take exactly ``size`` bytes on the wire.

  Parameters
  ----------
  size : int
    The wanted size of the record, in bytes.
  key : str
    Its composed key.
  owner : any
    Its owner.
  ver : list, optional
    Its stamp; None by default.

  Returns
  -------
  str
    A run of ``"x"`` of the length that brings ``wire_size(key, value, owner, ver=ver)`` to ``size``.

  Examples
  --------
  >>> wire_size(KEY, filler(1000, KEY, "peer1"), "peer1")
  1000
  """
  # One byte per character: the size of the record with an empty value, then the rest.
  return "x" * (size - wire_size(key, "", owner, ver=ver))


class TestSizeCap(unittest.TestCase):
  """A record over 256 KiB on the wire is refused wherever a record is stored, in every mode, under its own word."""

  def test_a_record_at_the_cap_is_stored_and_one_byte_more_is_refused_on_every_path(self):
    """A local write, a peer's store and a sync copy of exactly 262,144 bytes are stored; one byte more is refused
    as too_large (nothing stored, sent or confirmed, the order rule not run), with every switch 'off', 'report' or
    'on'; the writer and its receiver measure the same record, the stamp included; a delete of a record over the cap
    that a replica already holds is still taken."""
    # Each mode: the cap has no switch.
    for mode in ("off", "report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # The stamps the stores carry: none is made or read in 'off'.
        mine, theirs = (None, None) if mode == "off" else ([NOW_MS, "node"], [NOW_MS + 5, "peer1"])
        # A newer stamp of the same length, for the store that is refused.
        later = theirs and [NOW_MS + 9, "peer1"]
        # A peer's store of exactly the cap: stored and confirmed.
        store = node(mode)
        # Its value, sized with the stamp the receiver keeps.
        value = filler(CAP, KEY, "peer1", theirs)
        # Confirmed once.
        self.assertEqual((len(deliver(store, KEY, value, "peer1", theirs)), KEY in store.storage), (1, True))
        # One byte more, to another key: not confirmed and not stored ('on' answers the stamped store with the word).
        self.assertEqual((answers(deliver(store, OTHER, filler(CAP + 1, OTHER, "peer1", later), "peer1", later)),
                          OTHER in store.storage), (["too_large"] if mode == "on" else [], False))
        # Counted once under its word; the order rule did not run on it (no ver_ word, and the clock is still at the
        # first store's stamp, not at the refused store's newer one).
        self.assertEqual((getattr(store, STATS), getattr(store, CLOCK)), (
          {BUCKET + "/remote/changed": 1, BUCKET + "/remote/refused_too_large": 1}, (theirs or [0])[0]))
        # A local write of exactly the cap, its own stamp included: stored and queued for the peers.
        writer, info = node(mode), {}
        # Its value.
        value = filler(CAP, KEY, "node", mine)
        # Stored here without waiting.
        self.assertEqual((writer._set_value(KEY, value, owner="node", wait=False, result_info=info), info),
                         (True, {"outcome": "changed"}))
        # The store it queued.
        sent = getattr(writer, OPS)[-1]
        # A receiver takes that store: it measures the same bytes as the writer did.
        self.assertEqual(len(deliver(node(mode, address="peer1", peers=("node",)), KEY, sent["value"], "node",
                                     sent.get("ver"), owner=sent["owner"])), 1)
        # One byte more: refused at once, under its word.
        self.assertEqual((writer._set_value(OTHER, filler(CAP + 1, OTHER, "node", mine and [NOW_MS + 1, "node"]),
                                            owner="node", wait=False, result_info=info), info),
                         (False, {"outcome": "refused_too_large"}))
        # Nothing stored and nothing more queued for it.
        self.assertEqual((OTHER in writer.storage, len(getattr(writer, OPS))), (False, 1))
        # A sync answer with one copy at the cap and one a byte over it.
        store = node(mode)
        # Only the first is merged.
        self.assertEqual(getattr(store, MERGE)("players", {
          KEY: record(KEY, filler(CAP, KEY, "peer1", theirs), "peer1", theirs),
          OTHER: record(OTHER, filler(CAP + 1, OTHER, "peer1", theirs), "peer1", theirs),
        }), 1)
        # The other is skipped and counted.
        self.assertEqual((sorted(store.storage), getattr(store, STATS)),
                         ([KEY], {BUCKET + "/hsync/refused_too_large": 1}))
        # A value over the cap, as a replica can hold it from before the cap.
        old = "x" * 300_000
        # A replica holding two such records.
        store = node(mode, storage={KEY: record(KEY, old, "peer1"), OTHER: record(OTHER, old, "peer1")})
        # A delete is a small record: a local one (it does not wait) and a peer's are taken, the second confirmed.
        self.assertEqual((store._set_value(KEY, None, owner="node"), len(deliver(store, OTHER, None, "peer1", later))),
                         (True, 1))
        # Both keys are gone.
        self.assertEqual([key for key in (KEY, OTHER) if key in store.storage], [])

  def test_the_bytes_that_travel_are_counted_not_the_characters(self):
    """A value of 140,000 two-byte characters (280,000 bytes of UTF-8, fewer characters than the cap) is refused."""
    # A replica.
    store = node("report")
    # Fewer characters than the cap, more bytes.
    value = "\u00e9" * 140_000
    # Refused from a peer.
    self.assertEqual((len(value) < CAP, deliver(store, KEY, value, "peer1"), KEY in store.storage),
                     (True, [], False))

  def test_every_field_a_peer_fills_counts_toward_the_cap(self):
    """A small value with an owner, a readonly mark, a token or a stamp address of 300,000 characters is refused from
    a peer and skipped in a sync answer; so is a sync copy that differs from the record held in one such field only
    (a copy equal to it field for field is the only one not measured)."""
    # More than the cap in one field.
    big = "x" * 300_000
    # Each field a store carries besides the key and the value.
    for field, value in (("owner", big), ("readonly", big), ("token", big), ("ver", [NOW_MS, big])):
      # One sub-test each.
      with self.subTest(field=field):
        # A replica in 'report', where a stamp that names another address than its sender is only counted.
        store = node("report")
        # A store of "v" with that field, as the payload handler passes it.
        store._ChainStoreBasePlugin__exec_store({"key": KEY, "value": "v", "owner": "peer1", field: value},
                                                peers="peer1")
        # Not stored, not confirmed.
        self.assertEqual((KEY in store.storage, store.sent_payloads), (False, []))
        # The same record as a copy of a sync answer: skipped.
        self.assertEqual(getattr(store, MERGE)("players", {KEY: {**record(KEY, "v", "peer1"), field: value}}), 0)
        # Counted on both paths.
        self.assertEqual(getattr(store, STATS),
                         {BUCKET + "/remote/refused_too_large": 1, BUCKET + "/hsync/refused_too_large": 1})
    # The record a replica holds, stamped.
    held = record(KEY, "v", "peer1", [NOW_MS, "peer1"])
    # A copy of it with one field over the cap: its value, or one of the fields above (the stamp a newer one).
    for field, value in (("value", big), ("owner", big), ("readonly", big), ("token", big), ("ver", [NOW_MS + 5, big])):
      # One sub-test each.
      with self.subTest(held=field):
        # A replica holding the record: a copy equal to it field for field would not be measured.
        store = node("report", storage={KEY: held})
        # This copy differs in one field, so it is measured and skipped.
        self.assertEqual(getattr(store, MERGE)("players", {KEY: {**held, field: value}}), 0)
        # The held record stays as it was, its stamp included.
        self.assertEqual(store.storage[KEY], held)

class TestUnencodable(unittest.TestCase):
  """A record the wire's encoder or UTF-8 cannot take is refused wherever a record is stored, under its own word."""

  def test_a_string_utf8_cannot_encode_is_refused_in_a_key_or_a_value_on_every_path(self):
    """A lone surrogate in the key or in the value makes a local write, a peer's store and a sync copy refused as
    unencodable in every mode, so the replica's sync answer can still be encoded for sending."""
    # A key of the namespace that holds the string.
    bad_key = _hset_key("players", "gamma") + SURROGATE
    # Each mode.
    for mode in ("off", "report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A replica holding one good record.
        store, info = node(mode, storage={OTHER: record(OTHER, "fine", "peer1")}), {}
        # The string in the value, then in the key.
        for key, value in ((KEY, {"name": SURROGATE}), (bad_key, "v")):
          # A local write: refused at once under its word.
          self.assertEqual((store._set_value(key, value, owner="node", wait=False, result_info=info), info),
                           (False, {"outcome": "refused_unencodable"}))
          # A peer's store: not confirmed.
          self.assertEqual(deliver(store, key, value, "peer1"), [])
          # A sync copy: skipped.
          self.assertEqual(getattr(store, MERGE)("players", {key: record(key, value, "peer1")}), 0)
        # Nothing but the good record is held, and nothing was queued for the peers.
        self.assertEqual((sorted(store.storage), len(getattr(store, OPS))), ([OTHER], 0))
        # Each refusal counted under the bucket of its key.
        self.assertEqual(getattr(store, STATS), {
          BUCKET + "/local/refused_unencodable": 2, BUCKET + "/remote/refused_unencodable": 2,
          BUCKET + "/hsync/refused_unencodable": 2,
        })
        # The namespace's sync answer, encoded as the transport encodes a message and then as UTF-8: it does not raise.
        _JSONSerializationMixin.safe_json_dumps(getattr(store, EXPORT)("players"), ensure_ascii=False).encode("utf-8")

  def test_a_value_no_encoder_takes_is_refused_and_one_the_encoder_converts_is_stored(self):
    """A local write of a set, or of a value that holds itself, is refused as unencodable; numpy numbers and a
    datetime, which the wire's encoder turns into JSON, are stored as before."""
    # A replica.
    store, info = node("report"), {}
    # A set: no encoder takes it.
    self.assertEqual((store._set_value(KEY, {"tags": {"a", "b"}}, owner="node", wait=False, result_info=info), info),
                     (False, {"outcome": "refused_unencodable"}))
    # A value that holds itself.
    loop = {}
    # The circle.
    loop["again"] = loop
    # Refused under the same word.
    self.assertEqual((store._set_value(KEY, loop, owner="node", wait=False, result_info=info)), False)
    # Its word.
    self.assertEqual(info, {"outcome": "refused_unencodable"})
    # Values the encoder converts.
    value = {"n": np.int64(5), "x": np.float32(1.5), "at": datetime.datetime(2026, 1, 1)}
    # Stored and queued.
    self.assertEqual((store._set_value(KEY, value, owner="node", wait=False, result_info=info), info,
                      store.storage[KEY]["value"] is value), (True, {"outcome": "changed"}, True))

  def test_a_refused_unencodable_store_is_not_answered_and_a_too_large_one_is(self):
    """With the order switch 'on' a stamped store refused as unencodable gets no answer (its key or stamp may hold
    the string no message carries), and one refused as too_large gets the usual refusal answer."""
    # A replica that answers refused stamped stores.
    store = node("on")
    # A stamped store whose key holds the string: nothing comes back.
    self.assertEqual(deliver(store, KEY + SURROGATE, "v", "peer1", [NOW_MS, "peer1"]), [])
    # A stamped store over the cap: answered with its word.
    self.assertEqual(answers(deliver(store, KEY, "x" * 300_000, "peer1", [NOW_MS, "peer1"])), ["too_large"])


class TestNewKeysPerSender(unittest.TestCase):
  """New keys created by a sender outside the replication peers and the oracle registries are counted per sender."""

  def test_only_a_new_key_from_a_sender_outside_the_peers_and_the_registries_is_counted(self):
    """A peer's store that creates a key outside the protected namespaces counts new_keys/<sender> when its sender is
    neither a replication peer nor in a registry, and is applied; an overwrite, a delete, a refused store, a key of
    a protected namespace, a registry member's, a replication peer's and a local write count nothing, and the
    registries are not read for a replication peer."""
    # Each mode: the count has no switch.
    for mode in ("off", "report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # The signed store: replication peers peer1 and peer2, dAuth registry node and peer1, oracle registry oracle.
        store = node(mode, signed=True)
        # How often the oracle registry is read.
        reads = []
        # The registry read, counted.
        store.bc.get_eth_oracles = lambda: reads.append(1) or ["oracle"]
        # The stamp a store of this sender carries (none in 'off').
        stamp = lambda sender, ms=0: None if mode == "off" else [NOW_MS + ms, sender]  # noqa: E731
        # A replication peer creates a key: applied, not counted, no registry read.
        self.assertEqual((len(deliver(store, KEY, "v", "peer2", stamp("peer2"))), reads), (1, []))
        # A registry member outside the replication peers creates a key: applied, not counted.
        self.assertEqual(len(deliver(store, _hset_key("players", "o"), "v", "oracle", stamp("oracle"))), 1)
        # A sender outside both creates two keys: applied.
        self.assertEqual([len(deliver(store, _hset_key("players", name), "v", "worker", stamp("worker")))
                          for name in ("w1", "w2")], [1, 1])
        # It overwrites one, deletes it, sends a record over the cap to a new key and deletes a key not held: none is
        # a new key stored.
        for name, value, ms in (("w1", "w", 1), ("w1", None, 2), ("w3", "x" * 300_000, 3), ("w4", None, 4)):
          # One store each.
          deliver(store, _hset_key("players", name), value, "worker", stamp("worker", ms))
        # A node operation it signed itself, in a protected namespace: applied, not counted.
        operation = {"sequence": 1, "operation_id": "op", "node_addr": "worker", "attempt": "deploy",
                     "EE_SENDER": "worker", "EE_SIGN": "valid"}
        # Stored and confirmed.
        self.assertEqual(len(deliver(store, _hset_key("DEEPLOY_NODE_OPERATIONS", "op|deploy|worker"), operation,
                                     "worker", stamp("worker", 5))), 1)
        # A local write of a new key.
        store._set_value(_hset_key("players", "mine"), "v", owner="node", wait=False)
        # Only the two keys the outside sender created are counted, under its address.
        self.assertEqual({label: count for label, count in getattr(store, STATS).items()
                          if label.startswith("new_keys")}, {"new_keys/worker": 2})


if __name__ == "__main__":
  # Run this module alone.
  unittest.main()
