"""
Offline tests of the chain store's record size cap, of the refusal of a record no message can carry, and of the
count of new keys per sender outside the replication peers and the oracle registries, with the limit it can be given.
"""
# A datetime value, which the wire's encoder turns into text.
import datetime
# The independent measure of a record's wire form, and the wire form of a sync answer.
import json
# The data folder of a started store.
import tempfile
# The test framework.
import unittest
# The stand-in for the SerializationManager on_init registers its save with.
from types import SimpleNamespace
# Replaces that manager for one start.
from unittest import mock

# A numpy value, which the wire's encoder turns into a plain number.
import numpy as np
# The encoder a node uses on the wire (the plugin's json_dumps).
from ratio1.logging.logger_mixins.json_serialization_mixin import _JSONSerializationMixin

# The manager on_init registers its save with, replaced during a start.
from naeural_core.main.serialization_manager import SerializationManager

# The counter attribute's private name, and the harness that runs the real on_init.
from test_chain_store_accounting import STATS, _StartHarness
# The composer of a hash key.
from test_chain_store_hsync import _hset_key
# The order tests' store builder, deliverer of a live store, record builder, constants and private names.
from test_chain_store_order import BUCKET, CLOCK, EXPORT, KEY, MERGE, NOW_MS, answers, deliver, make, record
# The builder of a signed deployment operation as a replica holds it.
from test_chain_store_resync import operation

# The cap, in bytes of the record's wire form.
CAP = 524_288
# A second field of the namespace "players".
OTHER = _hset_key("players", "beta")
# The send queue of local writes, by its private name.
OPS = "_ChainStoreBasePlugin__ops"
# A string UTF-8 cannot encode: a lone surrogate, as the JSON escape "\ud800" in a peer's message decodes to.
SURROGATE = "bad \ud800 text"
# The limit of new keys per outside sender, as on_init reads it, by its private name.
LIMIT = "_ChainStoreBasePlugin__new_keys_max"


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
  """A record over 512 KiB on the wire is refused wherever a record is stored, in every mode, under its own word."""

  def test_a_record_at_the_cap_is_stored_and_one_byte_more_is_refused_on_every_path(self):
    """A local write, a peer's store and a sync copy of exactly 524,288 bytes are stored; one byte more is refused
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
        old = "x" * 600_000
        # A replica holding two such records.
        store = node(mode, storage={KEY: record(KEY, old, "peer1"), OTHER: record(OTHER, old, "peer1")})
        # A delete is a small record: a local one (it does not wait) and a peer's are taken, the second confirmed.
        self.assertEqual((store._set_value(KEY, None, owner="node"), len(deliver(store, OTHER, None, "peer1", later))),
                         (True, 1))
        # Both keys are gone.
        self.assertEqual([key for key in (KEY, OTHER) if key in store.storage], [])

  def test_the_bytes_that_travel_are_counted_not_the_characters(self):
    """A value of 280,000 two-byte characters (560,000 bytes of UTF-8, fewer characters than the cap) is refused."""
    # A replica.
    store = node("report")
    # Fewer characters than the cap, more bytes.
    value = "\u00e9" * 280_000
    # Refused from a peer.
    self.assertEqual((len(value) < CAP, deliver(store, KEY, value, "peer1"), KEY in store.storage),
                     (True, [], False))

  def test_every_field_a_peer_fills_counts_toward_the_cap(self):
    """A small value with an owner, a readonly mark, a token or a stamp address of 600,000 characters is refused from
    a peer and skipped in a sync answer; so is a sync copy that differs from the record held in one such field only
    (a copy equal to it field for field is the only one not measured)."""
    # More than the cap in one field.
    big = "x" * 600_000
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

  def test_a_sync_copy_is_measured_as_the_record_it_would_be_stored_as(self):
    """A sync copy is measured with the owner, readonly mark and token the namespace rules store it with, not with
    those its sender gave it: a job record whose value names a coordinator of 300,000 characters would be stored
    with that owner, over the cap, and is skipped, as the same record is refused as a live store; a signed
    operation sent with an owner of 600,000 characters is stored with its coordinator as owner, and is merged; a
    copy that would be stored as the record already held is not measured, also when that record is over the cap."""
    # A field of the job secrets, and their bucket.
    job = _hset_key("DAUTH_JOB_SECRETS", "9")
    # A value without a revision that names a long coordinator: the namespace rule stores it as the record's owner.
    grown = {"coordinator": "c" * 300_000, "note": "x"}
    # Each mode: the cap has no switch.
    for mode in ("off", "report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A dAuth member's replica ("node"; "peer1" is a member too, "oracle" is in the oracle registry).
        store = node(mode, signed=True)
        # As its sender gives it (owner "o") the copy is under the cap; as it would be stored, over it.
        self.assertEqual((wire_size(job, grown, "o") < CAP, wire_size(job, grown, grown["coordinator"]) > CAP),
                         (True, True))
        # The stamp the copy and the store carry (none is read in 'off').
        stamp = None if mode == "off" else [NOW_MS, "peer1"]
        # Skipped as a sync copy.
        self.assertEqual(getattr(store, MERGE)("DAUTH_JOB_SECRETS", {job: record(job, grown, "o", stamp)}), 0)
        # Refused as a live store from a dAuth member (answered in 'on' when stamped).
        self.assertEqual(answers(deliver(store, job, grown, "peer1", stamp, owner="o")),
                         ["too_large"] if mode == "on" else [])
        # Not held, and counted once on each path under the same word and no other: the order rule ran on neither,
        # so the stamp clock did not move to their stamp.
        self.assertEqual((job in store.storage, getattr(store, STATS), getattr(store, CLOCK)), (False, {
          job[:14] + "/hsync/refused_too_large": 1, job[:14] + "/remote/refused_too_large": 1}, 0))
        # A signed operation as a replica holds it, and a copy of it whose sender names an owner over the cap.
        key, held = operation("x", 1)
        # The copy's owner as sent.
        sent = {**held, "owner": "o" * 600_000}
        # Merged: the rule stores the coordinator as owner, so the record stored is a small one.
        self.assertEqual((getattr(store, MERGE)("DEEPLOY_OPERATIONS", {key: sent}), store.storage[key]["owner"]),
                         (1, "oracle"))
        # A replica that holds a record over the cap, from before the cap.
        store = node(mode, storage={KEY: record(KEY, "x" * 600_000, "peer1")})
        # A copy equal to it: nothing of it is stored, so it is not measured and counts nothing.
        self.assertEqual((getattr(store, MERGE)("players", {KEY: record(KEY, "x" * 600_000, "peer1")}),
                          getattr(store, STATS)), (0, {}))

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
    self.assertEqual(answers(deliver(store, KEY, "x" * 600_000, "peer1", [NOW_MS, "peer1"])), ["too_large"])


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
        for name, value, ms in (("w1", "w", 1), ("w1", None, 2), ("w3", "x" * 600_000, 3), ("w4", None, 4)):
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

  def test_with_a_limit_a_sender_is_refused_once_its_count_of_the_period_reached_it(self):
    """With CSTORE_NEW_KEYS_MAX 2, an outside sender's third new key of a CSTORE_STATS period is refused under its
    own word: not stored, not confirmed, not counted as a new key, and answered in 'on'. Its overwrites and deletes,
    another outside sender, a replication peer and a registry member are not refused, and after the next
    CSTORE_STATS line the sender creates keys again."""
    # Each mode: the limit has no switch; only the answer to a refused stamped store follows the order switch.
    for mode in ("off", "report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # The signed store: replication peers peer1 and peer2, dAuth registry node and peer1, oracle registry oracle.
        store = node(mode, signed=True)
        # A limit of two new keys per outside sender and period, as on_init would read it from the config.
        setattr(store, LIMIT, 2)
        # The stamp a store of this sender carries (none in 'off').
        stamp = lambda sender, ms=0: None if mode == "off" else [NOW_MS + ms, sender]  # noqa: E731
        # The sender's first two new keys: applied and confirmed.
        self.assertEqual([len(deliver(store, _hset_key("players", name), "v", "worker", stamp("worker")))
                          for name in ("w1", "w2")], [1, 1])
        # Its third: refused. Not confirmed; with the order switch 'on' the stamped store gets the refusal answer.
        self.assertEqual(answers(deliver(store, _hset_key("players", "w3"), "v", "worker", stamp("worker", 1))),
                         ["new_keys"] if mode == "on" else [])
        # Not stored.
        self.assertNotIn(_hset_key("players", "w3"), store.storage)
        # It still overwrites a key it created and deletes it: neither creates a key.
        self.assertEqual([len(deliver(store, _hset_key("players", "w1"), value, "worker", stamp("worker", ms)))
                          for value, ms in (("w", 2), (None, 3))], [1, 1])
        # Another outside sender has its own count: its first new key is applied.
        self.assertEqual(len(deliver(store, _hset_key("players", "x1"), "v", "worker2", stamp("worker2"))), 1)
        # A replication peer and a registry member create three keys each: never counted, never refused.
        self.assertEqual([len(deliver(store, _hset_key("players", sender + str(index)), "v", sender, stamp(sender)))
                          for sender in ("peer2", "oracle") for index in range(3)], [1] * 6)
        # The refused store has its own word under the key's bucket; the count stays at the two keys it created.
        self.assertEqual({label: count for label, count in getattr(store, STATS).items() if "new_keys" in label},
                         {"new_keys/worker": 2, "new_keys/worker2": 1, BUCKET + "/remote/refused_new_keys": 1})
        # The lap that writes the next CSTORE_STATS line, 300 s later, restarts the counts.
        store._now += store.CS_STATS_INTERVAL
        # No peer refresh, resync or census in this lap: only the line.
        store._ChainStoreBasePlugin__last_chain_peers_refresh = store._now
        # The resync not due, and the census hour the current one.
        store._ChainStoreBasePlugin__resync_next, store._ChainStoreBasePlugin__census_hour = (
          store._now + 60, int(store._now // 3600))
        # The lap.
        store.process()
        # A new period: the sender's next new key is applied and confirmed again.
        self.assertEqual(len(deliver(store, _hset_key("players", "w3"), "v", "worker", stamp("worker", 300_000))), 1)
        # Stored now.
        self.assertIn(_hset_key("players", "w3"), store.storage)

  def test_the_refusal_starts_exactly_at_the_limit_and_zero_refuses_nothing(self):
    """With CSTORE_NEW_KEYS_MAX n above 0, exactly n new keys of one outside sender are stored in a period and the
    next ones are refused (n = 1 and n = 3); with 0, the default, every one is stored and counted, in every mode."""
    # Zero and two limits, to pin both ends of the comparison; each mode, as the limit has no switch.
    for limit in (0, 1, 3):
      # Each mode.
      for mode in ("off", "report", "on"):
        # One sub-test each.
        with self.subTest(limit=limit, mode=mode):
          # The signed store, whose registries do not hold the sender.
          store = node(mode, signed=True)
          # The limit, as on_init would read it.
          setattr(store, LIMIT, limit)
          # Five new keys from the outside sender: one confirmation for each stored one.
          stored = [len([body for body in deliver(store, _hset_key("players", "w%d" % index), "v", "worker",
                                                  None if mode == "off" else [NOW_MS + index, "worker"])
                         if "refused" not in body]) for index in range(5)]
          # How many the limit lets through: all with 0, else exactly the limit.
          taken = limit or 5
          # The first ones are stored, the rest refused.
          self.assertEqual(stored, [1] * taken + [0] * (5 - taken))
          # The count holds the keys created; each refused store has the refusal's own word.
          self.assertEqual({label: count for label, count in getattr(store, STATS).items() if "new_keys" in label},
                           {"new_keys/worker": taken, **({BUCKET + "/remote/refused_new_keys": 5 - taken}
                                                         if taken < 5 else {})})

  def test_the_limit_is_read_once_at_start_from_the_config(self):
    """on_init reads CSTORE_NEW_KEYS_MAX once: the harness default 0 and a numeric str are taken as ints, and a
    value that is no number stops the start."""
    # A data folder for the starts, removed after the test.
    folder = tempfile.TemporaryDirectory()
    # Removed after the test.
    self.addCleanup(folder.cleanup)
    # A manager that takes the save registration of a start and does nothing with it.
    manager = mock.patch.object(SerializationManager, "instance", SimpleNamespace(register=lambda save, every: None))
    # In force for this test.
    manager.start()
    # Put back after the test.
    self.addCleanup(manager.stop)
    # The config value and what on_init keeps for it.
    for given, kept in ((0, 0), ("25", 25), (40, 40)):
      # One sub-test each.
      with self.subTest(given=given):
        # A store to start.
        store = _StartHarness(folder.name)
        # The config value.
        store.cfg_cstore_new_keys_max = given
        # The real on_init.
        store.on_init()
        # Kept as an int.
        self.assertEqual(getattr(store, LIMIT), kept)
    # A store whose value is no number.
    store = _StartHarness(folder.name)
    # A word.
    store.cfg_cstore_new_keys_max = "many"
    # The start stops on the conversion.
    with self.assertRaisesRegex(ValueError, "invalid literal for int"):
      # The real on_init.
      store.on_init()


if __name__ == "__main__":
  # Run this module alone.
  unittest.main()
