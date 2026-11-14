"""
Offline tests of the chain store's hourly CSTORE_CENSUS line: per namespace bucket, the records, unstamped records,
tombstones, readonly records, size and a digest that two replicas holding the same records share.
"""
# A datetime value, which the wire's encoder turns into text.
import datetime
# The census line's JSON.
import json
# The test framework.
import unittest
# The block engine and NetMon stand-ins of process().
from types import SimpleNamespace

# Numpy values, which the wire's encoder turns into plain numbers.
import numpy as np
# The encoder a node uses on the wire (the plugin's json_dumps).
from ratio1.logging.logger_mixins.json_serialization_mixin import _JSONSerializationMixin

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


class _ChangedDuringTheWalk(dict):
  """
  A replica whose records another thread changed after the census took its list of items.

  The dict holds the records as they are now; ``items`` gives them as they were when the census copied them, as a
  census that copied the items just before the change sees them.

  Parameters
  ----------
  copied : dict
    The records as the census copied them.
  now : dict
    The records as the replica holds them after the change.

  Examples
  --------
  >>> replica = _ChangedDuringTheWalk({"k": 1}, {})
  >>> (list(replica.items()), "k" in replica)
  ([('k', 1)], False)
  """

  def __init__(self, copied, now):
    """
    Hold ``now`` and remember ``copied`` for ``items``.

    Parameters
    ----------
    copied : dict
      The records as the census copied them.
    now : dict
      The records as the replica holds them after the change.

    Returns
    -------
    None
    """
    # The records as they are now.
    super().__init__(now)
    # The records as the census copied them.
    self.copied = copied

  def items(self):
    """
    Give the records as the census copied them.

    Returns
    -------
    list of tuple
      The copied ``(key, record)`` pairs.

    Examples
    --------
    >>> list(_ChangedDuringTheWalk({"k": 1}, {}).items())
    [('k', 1)]
    """
    # The census's copy, not the records held now.
    return list(self.copied.items())


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
    """The line holds no key, field name or value; the digest of a record in either dAuth secret namespace and of a
    record with a token does not change with the value but changes with the stamp (it hashes the key and the
    stamp), while that of an ordinary record changes with the value."""
    # A dAuth secret, a secret revision, a record with a token, and an ordinary record, each with a recognisable value.
    secret, ordinary = _hset_key("DAUTH_JOB_SECRETS", "7"), _hset_key("players", "visible-field")
    # A dAuth secret revision, whose value must not be hashed either.
    revision = _hset_key("DAUTH_JOB_SECRET_REVISIONS", "7|1")
    # The replica.
    store = make("report", storage={
      secret: record(secret, {"job_secrets": {"TOKEN": "super-secret"}}, "oracle", [NOW_MS, "oracle"]),
      revision: record(revision, {"job_secrets": {"TOKEN": "rev-secret"}}, "oracle", [NOW_MS, "oracle"]),
      "tok-key": {**record("tok-key", "token-guarded", "peer1", [NOW_MS, "peer1"]), "token": "t"},
      ordinary: record(ordinary, "plain-visible", "peer1", [NOW_MS, "peer1"]),
    })
    # The line.
    rows, line = census(store)
    # None of the keys, decoded field names or values appears in it.
    self.assertEqual([text for text in (secret, revision, "tok-key", ordinary, "visible-field", "super-secret",
                                        "rev-secret", "token-guarded", "plain-visible", "TOKEN") if text in line], [])
    # Every value changed, the stamps kept.
    for key, value in ((secret, {"job_secrets": {"TOKEN": "other"}}), (revision, {"job_secrets": {"TOKEN": "other"}}),
                       ("tok-key", "other"), (ordinary, "other")):
      # The new value.
      store.storage[key]["value"] = value
    # The new rows.
    again = census(store)[0]
    # The secrets' and the token record's digests kept; the ordinary record's changed.
    self.assertEqual([rows[bucket][5] == again[bucket][5]
                      for bucket in (secret[:14], revision[:14], "plain", ordinary[:14])], [True, True, True, False])
    # Then the stamps of the secrets and the token record raised, their values kept.
    for key in (secret, revision, "tok-key"):
      # A newer stamp.
      store.storage[key]["ver"] = [NOW_MS + 5, "oracle"]
    # The rows after it.
    third = census(store)[0]
    # Their digests change: the stamp is hashed.
    self.assertEqual([again[bucket][5] != third[bucket][5] for bucket in (secret[:14], revision[:14], "plain")],
                     [True, True, True])

  def test_a_writer_and_its_receivers_give_equal_records_the_same_row(self):
    """A writer's own values with int dict keys, mixed int and str keys, numpy numbers or a datetime give the same
    row as its receivers' copies, which hold them as the wire delivers them."""
    # The writer's own values, as an old own record can hold them.
    values = [{1: "a", 2: "b", 10: "c"}, {1: "a", "b": 2}, {"n": np.int64(5), "x": np.float32(1.5)},
              {"at": datetime.datetime(2026, 1, 1)}]
    # The keys they are held under.
    keys = [_hset_key("players", str(n)) for n in range(len(values))]
    # The writer, holding them as own records (confirms 0).
    writer = make("report", storage={key: {**record(key, value, "node", [NOW_MS, "node"]), "confirms": 0}
                                     for key, value in zip(keys, values)})
    # A receiver, holding each as the wire delivered it.
    receiver = make("report", "peer1", storage={
      key: record(key, json.loads(_JSONSerializationMixin.safe_json_dumps(value)), "node", [NOW_MS, "node"])
      for key, value in zip(keys, values)})
    # Both encode as a node does.
    for store in (writer, receiver):
      # The plugin's json_dumps.
      store.json_dumps = _JSONSerializationMixin.safe_json_dumps
    # The same rows.
    self.assertEqual(census(writer)[0], census(receiver)[0])

  def test_a_record_changed_during_the_walk_is_read_from_the_copy(self):
    """A readonly token record another thread deletes, and an own record a peer's write replaces, after the census
    copied the replica's items, are read whole from the copy: the token record counts as readonly and only its stamp
    is hashed, never its value, and the own record goes through the wire's round trip; the row equals that of a
    replica that still holds the copied records."""
    # The records as the census copied them: a readonly token record, and an own record with mixed dict keys (which
    # only the wire's round trip puts in canonical form).
    copied = {"tok-key": {**record("tok-key", "token-guarded", "peer1", [NOW_MS, "peer1"]), "token": "t",
                          "readonly": True},
              "own": {**record("own", {1: "a", "b": 2}, "node", [NOW_MS, "node"]), "confirms": 0}}
    # A replica that still holds them, and one where, during the walk, the token record was deleted and the own
    # record replaced by a peer's copy.
    held, raced = make("report", storage=copied), make("report")
    # The changed replica as the census walks it.
    raced._ChainStoreBasePlugin__chain_storage = _ChangedDuringTheWalk(
      copied, {"own": record("own", "new", "peer1", [NOW_MS + 1, "peer1"])})
    # Both encode as a node does.
    for store in (held, raced):
      # The plugin's json_dumps.
      store.json_dumps = _JSONSerializationMixin.safe_json_dumps
    # The same row, which counts both records, one readonly, none unencodable.
    self.assertEqual((census(raced)[0], census(held)[0]["plain"][:4] + census(held)[0]["plain"][6:]),
                     (census(held)[0], [2, 0, 0, 1, 0]))

  def test_the_census_does_not_raise_on_what_a_replica_can_hold(self):
    """A key that holds a lone surrogate (decoded from a JSON escape), a record of an older shape without readonly
    and token, and own values no encoder takes (a set, a circular list, a list nested too deep) still give a line:
    those values are counted as unencodable and left out of the size and the digest."""
    # A key decoded from a JSON escape, and a list that holds itself.
    surrogate, circular = json.loads('"plain\\ud800k"'), []
    # Circular.
    circular.append(circular)
    # A list nested deeper than the encoder's recursion limit.
    deep = []
    # Each level.
    for _ in range(100_000):
      # One level more.
      deep = [deep]
    # Own records of a set (TypeError), the circular list (ValueError) and the deep list (RecursionError).
    odd = {name: {**record(name, value, "node"), "confirms": 0}
           for name, value in (("odd-set", {1, 2}), ("odd-circular", circular), ("odd-deep", deep))}
    # Two replicas holding the surrogate key's record and a record of an older shape.
    store, without = (make("report", storage={
      surrogate: record(surrogate, "v", "peer1"), "legacy": {"key": "legacy", "value": "old", "owner": "peer1"}})
      for _ in range(2))
    # The first also holds the unencodable records, added as they are (a copy of the deep list would raise itself).
    store._ChainStoreBasePlugin__chain_storage.update(odd)
    # Both encode as a node does.
    for replica in (store, without):
      # The plugin's json_dumps.
      replica.json_dumps = _JSONSerializationMixin.safe_json_dumps
    # The two lines' plain rows.
    row, other = census(store)[0]["plain"], census(without)[0]["plain"]
    # Five records, all unstamped, sizes of "v" and "old" only, three unencodable; the same digest as without them.
    self.assertEqual((row[:5], row[6], row[5]), ([5, 5, 0, 0, len('"v"') + len('"old"')], 3, other[5]))

  def test_the_census_runs_at_the_first_lap_after_each_full_hour(self):
    """Two replicas started 31.5 min apart (their stats lines 90 s out of step) write the census line at the same
    moments, their first lap after each full UTC hour, and none before the first full hour after their start."""
    # Per start, the times of the laps that wrote a census line.
    times = {}
    # Each start.
    for start in (NOW, NOW + 1890):
      # A replica with one record.
      store = make("report", storage={ALPHA: record(ALPHA, "v", "peer1", [NOW_MS, "peer1"])})
      # Its whitelist and NetMon for the peer refresh of process().
      store.bc = SimpleNamespace(get_whitelist=lambda with_prefix=False: ["peer1", "peer2"])
      # Both peers running.
      store.netmon = SimpleNamespace(network_node_simple_status=lambda peer, **kwargs: "ONLINE")
      # As on_init leaves them at this start: the stats line and the census hour counted from it.
      store._ChainStoreBasePlugin__cs_stats_last, store._ChainStoreBasePlugin__census_hour = start, int(start // 3600)
      # Its census laps.
      times[start] = []
      # A lap every 10 s until 3 h after NOW.
      for at in range(int(start), int(NOW) + 3 * 3600, 10):
        # The lap's clock.
        store._now = at
        # The messages before it.
        before = len(store.messages)
        # The lap.
        store.process()
        if any(msg.startswith("CSTORE_CENSUS") for msg in store.messages[before:]):
          # A census lap.
          times[start].append(at - NOW)
    # The full hours after NOW fall 2,800 s, 6,400 s and 10,000 s after it: both replicas take the census there.
    self.assertEqual(times, {NOW: [2800, 6400, 10000], NOW + 1890: [2800, 6400, 10000]})


if __name__ == "__main__":
  # Run this module alone.
  unittest.main()
