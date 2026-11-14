"""
Offline tests of the chain store's own resync of the signed namespaces: which node asks which peers for which
namespace and when, how every answer is merged without a waiting caller, and the merge taking a copy of the value
held without checking its signature again.
"""
# A record held from before a rule, copied for a sync answer.
import copy
# The digest of a value's canonical text, the tests' stand-in for a signature over it.
import hashlib
# The ids of a store's requests.
import itertools
# The canonical text of a value: its JSON with sorted keys, where 5 and 5.0 are two texts.
import json
# The data folder of a started store.
import os
# Its temporary place.
import tempfile
# The test framework.
import unittest
# The stand-in for the SerializationManager on_init registers its save with.
from types import SimpleNamespace
# Fixes the random wait and the pick of the peers for one test.
from unittest import mock

# The manager on_init registers its save with, replaced during a start.
from naeural_core.main.serialization_manager import SerializationManager

# The counter attribute's private name, the signed pointer fixture and the harness that runs the real on_init.
from test_chain_store_accounting import STATS, SIGNED_POINTER, _StartHarness
# The harness, its fake network, the block engine and NetMon stand-ins of a peer refresh, and the key composers.
from test_chain_store_hsync import (_ChainStoreRuntimeHarness, _FakeNetwork, _SummaryControlNetmon,
                                    _WhitelistBlockEngine, _hset_index, _hset_key)
# The order tests' store builder, record builder, constants and the private names of the merge and the dirty mark.
from test_chain_store_order import DIRTY, MERGE, NOW, NOW_MS, make, record

# The resync step, the time of its next request and the turn of its namespaces, by their private names.
RESYNC, NEXT, TURN = ("_ChainStoreBasePlugin__maybe_resync", "_ChainStoreBasePlugin__resync_next",
                      "_ChainStoreBasePlugin__resync_turn")
# The globals of the chain store's module, where its random wait and its pick of peers are looked up.
GLOBALS = _ChainStoreRuntimeHarness._hsync.__globals__
# The five signed namespaces, in the order a round asks them.
NAMESPACES = ("DEEPLOY_OPERATIONS", "DEEPLOY_NODE_OPERATIONS", "DEEPLOY_DEPLOYED_JOBS", "DAUTH_JOB_SECRETS",
              "NODE_KEY_ROTATIONS")
# The bucket of the deployment operations, under which their resync is counted.
OPS_BUCKET = _hset_index("DEEPLOY_OPERATIONS")


def operation(name, sequence, note="a"):
  """
  Build one deployment operation, signed by the coordinator "oracle", as a chain store holds it.

  Parameters
  ----------
  name : str
    The operation's id, also its field in ``DEEPLOY_OPERATIONS``.
  sequence : int
    Its signed sequence: a higher one replaces a lower one.
  note : str, optional
    Free content, to tell two operations of one sequence apart; ``"a"`` by default.

  Returns
  -------
  tuple
    ``(key, record)``: the composed key and the stored record (owner the coordinator, readonly, unstamped).

  Examples
  --------
  >>> operation("x", 1)[1]["value"]["sequence"]
  1
  """
  # The composed key of the operation's field.
  key = _hset_key("DEEPLOY_OPERATIONS", name)
  # The signed value: the test block engine accepts the signature "valid", and "oracle" is in the oracle registry.
  value = {"sequence": sequence, "operation_id": name, "coordinator": "oracle", "EE_SENDER": "oracle",
           "EE_SIGN": "valid", "note": note}
  # As the merge stores a signed operation: owned by its coordinator and readonly.
  return key, {**record(key, value, "oracle"), "readonly": True}


def text_signed(value):
  """
  Sign a value over its canonical text, as a real signature covers it: equal values of another text do not verify.

  The harness's block engine accepts any value marked ``EE_SIGN: "valid"``. The real one hashes the value's
  canonical JSON, where the int 5 and the float 5.0 are two texts although Python holds them equal. This gives a
  value such a signature: the SHA-256 of its JSON with sorted keys, without the signature field itself.

  Parameters
  ----------
  value : dict
    The value to sign; an ``EE_SIGN`` it already carries is replaced.

  Returns
  -------
  dict
    A copy of ``value`` whose ``EE_SIGN`` is the digest of the rest. A value verifies when it equals the result
    of signing it again: ``value == text_signed(value)``.

  Examples
  --------
  >>> good = text_signed({"n": 5})
  >>> good == text_signed(good), {**good, "n": 5.0} == text_signed({**good, "n": 5.0})
  (True, False)
  """
  # The value without its signature.
  body = {name: item for name, item in value.items() if name != "EE_SIGN"}
  # The digest of its canonical text as the signature.
  return {**body, "EE_SIGN": hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()}


def copy_or_fail(value):
  """
  Copy a value as the store does, and fail for the one marked bad: a merge that raises at one of its records.

  A test gives it to a store as its ``deepcopy``, which the merge calls first for each copy of an answer.

  Parameters
  ----------
  value : any
    The value to copy.

  Returns
  -------
  any
    A deep copy of ``value``.

  Raises
  ------
  RuntimeError
    For a dict whose ``note`` is ``"bad"``.

  Examples
  --------
  >>> copy_or_fail({"note": "a"})
  {'note': 'a'}
  """
  if isinstance(value, dict) and value.get("note") == "bad":
    # The failure, whatever its cause in a node.
    raise RuntimeError("copy failed")
  # Any other value, copied as the harness copies.
  return copy.deepcopy(value)


def oracle(mode, address="node", peers=("peer1", "oracle"), storage=None, network=None):
  """
  Build the chain store of one node of the tests' registry, with distinct request ids, optionally on a network.

  The registry is the signed store's: dAuth registry ``node`` and ``peer1``, oracle registry ``oracle``; every
  other address is in neither.

  Parameters
  ----------
  mode : str
    ``'off'``, ``'report'`` or ``'on'``, for every switch.
  address : str, optional
    The node's address; ``"node"`` by default.
  peers : sequence of str, optional
    Its replication peers; ``("peer1", "oracle")`` by default.
  storage : dict, optional
    Records it holds; none by default.
  network : _FakeNetwork, optional
    A network to register the store on, which then delivers what it sends; None (the default) only records it.

  Returns
  -------
  _ChainStoreRuntimeHarness
    The store. Its ``uuid`` gives ``"r1"``, ``"r2"``, ... so each request has its own id.

  Examples
  --------
  >>> oracle("report").uuid()
  'r1'
  """
  # The signed store of the order tests, in this mode.
  store = make(mode, address=address, peers=peers, storage=storage, signed=True)
  # A counter for the request ids.
  ids = itertools.count(1)
  # Distinct ids, as a node's uuid gives (the harness's own gives one fixed id).
  store.uuid = lambda: "r%d" % next(ids)
  if network is not None:
    # Reachable for requests and answers.
    network.register(store)
  # Ready.
  return store


def lap(store, at):
  """
  Run the resync step of ``store`` at ``at`` seconds after the tests' wall clock and give the requests it sent.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    The store.
  at : float
    Seconds after ``NOW``.

  Returns
  -------
  list of tuple
    One ``(sorted recipients, body)`` per message the step made the store send.

  Examples
  --------
  >>> lap(oracle("report"), 0)
  [(['oracle', 'peer1'], {'op': 'SHSYNC_REQ', 'request_id': 'r1', 'hkey': 'DEEPLOY_OPERATIONS'})]
  """
  # The lap's wall clock.
  store._now = NOW + at
  # Only what this lap sends.
  before = len(store.sent_payloads)
  # The resync step, as process() runs it each lap.
  getattr(store, RESYNC)()
  # Recipients and body of each message sent.
  return [(sorted(sent["node_addr"]), sent["data"]["CHAIN_STORE_DATA"]) for sent in store.sent_payloads[before:]]


def answer(store, request_id, sender, hkey, records):
  """
  Deliver one sync answer to ``store`` through its payload handler, as the network does.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    The store that asked.
  request_id : str
    The id of the request answered.
  sender : str
    The answering peer.
  hkey : str
    The namespace answered.
  records : list of tuple
    The ``(key, record)`` pairs of the answer's snapshot.

  Returns
  -------
  None

  Examples
  --------
  >>> answer(store, "r1", "peer1", "DEEPLOY_OPERATIONS", [operation("x", 2)])
  """
  # The answer as a peer's export carries it, through the handler.
  store.default_handler(store.make_response_payload(request_id=request_id, sender=sender, hkey=hkey,
                                                    snapshot=dict(records)))


def resync_counts(store):
  """
  Give the resync's counters of ``store``.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    The store.

  Returns
  -------
  dict
    The labels of the ``CSTORE_STATS`` counters that hold ``/resync/``, with their counts.

  Examples
  --------
  >>> resync_counts(oracle("report"))
  {}
  """
  # Only the resync's own labels; the order rule's counts of the merged copies are left out.
  return {label: count for label, count in getattr(store, STATS).items() if "/resync/" in label}


class TestResyncRequests(unittest.TestCase):
  """An oracle asks two registry members among its peers for one signed namespace per request, round after round."""

  def test_a_round_asks_the_five_namespaces_one_gap_apart_then_pauses(self):
    """With no random stretch a round asks DEEPLOY_OPERATIONS, DEEPLOY_NODE_OPERATIONS, DEEPLOY_DEPLOYED_JOBS,
    DAUTH_JOB_SECRETS and NODE_KEY_ROTATIONS 60 s apart, each of registry members only (the two dAuth-written ones
    of dAuth members only), then pauses 1800 s; nothing is asked, and no turn used, while there is no peer; each
    wait is stretched by 0.75 to 1.25 at random."""
    # The wait's random factor fixed at 1: random() gives 0.5, and the wait is its length times 0.75 + 0.5 / 2.
    with mock.patch.dict(GLOBALS, {"random": lambda: 0.5}):
      # A dAuth member with no peer yet.
      store = oracle("report", peers=())
      # Nothing asked at start, and the first namespace keeps its turn.
      self.assertEqual((lap(store, 0), getattr(store, TURN)), ([], 0))
      # Its peers appear: a dAuth member, an oracle, and a node in neither registry.
      store._ChainStoreBasePlugin__chain_peers = ["peer1", "oracle", "peer2"]
      # The laps of a round and the first of the next: who is asked for what, and nothing in between.
      asked = [(at, [(to, body["hkey"]) for to, body in lap(store, at)])
               for at in (10, 69, 70, 130, 190, 250, 310, 2049, 2050)]
      # The members among the peers; only the dAuth member for the two namespaces dAuth servers write.
      both, dauth = ["oracle", "peer1"], ["peer1"]
      # One namespace per gap, the registry members only, then the pause.
      self.assertEqual(asked, [
        (10, [(both, NAMESPACES[0])]), (69, []), (70, [(both, NAMESPACES[1])]), (130, [(both, NAMESPACES[2])]),
        (190, [(dauth, NAMESPACES[3])]), (250, [(dauth, NAMESPACES[4])]), (310, []), (2049, []),
        (2050, [(both, NAMESPACES[0])]),
      ])
    # The shortest and the longest wait after a request: 0.75 and (just under) 1.25 times the gap.
    for draw, wait in ((0.0, 45.0), (0.999, 74.97)):
      # One sub-test each.
      with self.subTest(draw=draw), mock.patch.dict(GLOBALS, {"random": lambda: draw}):
        # A fresh oracle with its peers.
        store = oracle("report")
        # Its first request.
        lap(store, 0)
        # The next one is due after the stretched gap.
        self.assertAlmostEqual(getattr(store, NEXT) - NOW, wait, places=3)

  def test_a_request_is_a_plain_sync_request_to_two_members_picked_among_the_peers(self):
    """The request is the namespace sync's own message (op, request id, hkey) sent to exactly two registry members
    among the replication peers, and it is registered with the peers asked and the time its answers are taken
    until."""
    # An oracle with four registry members and one outsider among its peers.
    store = oracle("report", peers=("peer1", "oracle", "oracle2", "oracle3", "peer2"))
    # Two more oracles in the registry.
    store.bc.get_eth_oracles = lambda: ["oracle", "oracle2", "oracle3"]
    # The first lap's requests.
    sent = lap(store, 5)
    # One message, to two distinct members, none of them the outsider.
    self.assertEqual((len(sent), len(set(sent[0][0])), set(sent[0][0]) <= {"peer1", "oracle", "oracle2", "oracle3"}),
                     (1, 2, True))
    # What a node of any release answers: the three fields of a sync request.
    self.assertEqual(sent[0][1], {"op": "SHSYNC_REQ", "request_id": "r1", "hkey": "DEEPLOY_OPERATIONS"})
    # Its entry in the table of pending sync requests.
    entry = store.pending_hsync["r1"]
    # Registered with the peers asked and the end of its wait, one gap later; no caller's response slot is filled.
    self.assertEqual((entry[store.CS_HKEY], sorted(entry[store.CS_PEERS]), entry[store.CS_RESPONSE], entry["until"]),
                     ("DEEPLOY_OPERATIONS", sent[0][0], None, NOW + 65))
    # Counted under the namespace's bucket.
    self.assertEqual(resync_counts(store), {OPS_BUCKET + "/resync/asked": 1})

  def test_only_an_oracle_resyncs_and_the_secrets_only_a_dauth_member(self):
    """An oracle outside the dAuth registry asks for the four other namespaces and skips DAUTH_JOB_SECRETS (its
    turn passes); a node in neither registry asks for nothing."""
    # No random stretch.
    with mock.patch.dict(GLOBALS, {"random": lambda: 0.5}):
      # "oracle" is in the oracle registry only; its peers are the two dAuth members and an outsider.
      store = oracle("report", address="oracle", peers=("node", "peer1", "peer2"))
      # The namespaces of one round's requests, lap by lap.
      asked = [[(to, body["hkey"]) for to, body in lap(store, at)] for at in (0, 60, 120, 180, 240)]
      # The secrets' lap sends nothing; the key rotations are asked of the dAuth members.
      self.assertEqual(asked, [[(["node", "peer1"], name)] if name != "DAUTH_JOB_SECRETS" else []
                               for name in NAMESPACES])
      # A node in neither registry, with the same peers.
      store = oracle("report", address="worker", peers=("node", "peer1", "peer2"))
      # A whole round of laps: nothing sent.
      self.assertEqual([lap(store, at) for at in (0, 60, 120, 180, 240)], [[], [], [], [], []])


class TestResyncAnswers(unittest.TestCase):
  """Every asked peer's answer is merged when it arrives, once, until the wait is over; only versioned values."""

  def test_every_answer_is_merged_not_only_the_first(self):
    """Two peers hold different newer operations; after one request the oracle holds both, in every mode, with the
    request, the two answers and the two merged records counted."""
    # Each mode: the resync has no switch.
    for mode in ("off", "report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A network that delivers at once.
        network = _FakeNetwork()
        # "peer1" holds operation x at sequence 2.
        oracle(mode, "peer1", ("node", "oracle"), dict([operation("x", 2)]), network)
        # "oracle" holds x at sequence 1 and another operation, y.
        oracle(mode, "oracle", ("node", "peer1"), dict([operation("x", 1), operation("y", 1)]), network)
        # The node that was away holds x at sequence 1 only.
        store = oracle(mode, storage=dict([operation("x", 1)]), network=network)
        # Its first request, answered by both peers within the lap.
        self.assertEqual([body["hkey"] for _, body in lap(store, 0) if body["op"] == "SHSYNC_REQ"],
                         ["DEEPLOY_OPERATIONS"])
        # It holds the newer x of one peer and the y of the other.
        self.assertEqual({key: held["value"]["sequence"] for key, held in store.storage.items()},
                         {operation("x", 2)[0]: 2, operation("y", 1)[0]: 1})
        # One request, two answers, two records merged.
        self.assertEqual(resync_counts(store), {OPS_BUCKET + "/resync/asked": 1, OPS_BUCKET + "/resync/answered": 2,
                                                OPS_BUCKET + "/resync/merged": 2})

  def test_an_answer_is_taken_once_per_asked_peer_and_only_until_the_wait_is_over(self):
    """An answer from a peer that was not asked, a second answer from one that was, and an answer after the wait
    change nothing; the lap that ends the wait forgets the request and leaves a blocking sync's entry alone."""
    # An oracle whose messages are only recorded.
    store = oracle("report")
    # No random stretch: the second request is due 60 s after the first.
    patcher = mock.patch.dict(GLOBALS, {"random": lambda: 0.5})
    # In force for this test.
    patcher.start()
    # Put back after it.
    self.addCleanup(patcher.stop)
    # Its first request, to both peers, waited for until 60 s later.
    lap(store, 0)
    # The entry of a blocking sync a caller is waiting on, as _hsync registers it: no end of wait.
    store.pending_hsync["caller"] = {store.CS_HKEY: "players", store.CS_PEERS: ["peer1"], store.CS_RESPONSE: None}
    # A node that was not asked answers with operation w: not taken.
    answer(store, "r1", "peer2", "DEEPLOY_OPERATIONS", [operation("w", 1)])
    # "peer1" answers with x: merged.
    answer(store, "r1", "peer1", "DEEPLOY_OPERATIONS", [operation("x", 1)])
    # "peer1" answers again with y: not taken.
    answer(store, "r1", "peer1", "DEEPLOY_OPERATIONS", [operation("y", 1)])
    # Only x is held, and "oracle" is the one peer whose answer is still awaited.
    self.assertEqual((sorted(store.storage), store.pending_hsync["r1"][store.CS_PEERS]),
                     ([operation("x", 1)[0]], ["oracle"]))
    # The last lap inside the wait keeps the request (and sends the next namespace's request, "r2").
    lap(store, 60)
    # Both are pending.
    self.assertEqual(sorted(store.pending_hsync), ["caller", "r1", "r2"])
    # The first lap after the wait forgets the first request and keeps the blocking sync's entry and the second.
    lap(store, 60.5)
    # The entries left.
    self.assertEqual(sorted(store.pending_hsync), ["caller", "r2"])
    # "oracle" answers the forgotten request with z: not taken.
    answer(store, "r1", "oracle", "DEEPLOY_OPERATIONS", [operation("z", 1)])
    # Still only x, from one counted answer.
    self.assertEqual((sorted(store.storage), resync_counts(store)[OPS_BUCKET + "/resync/answered"]),
                     ([operation("x", 1)[0]], 1))

  def test_an_answer_whose_merge_raises_leaves_what_it_merged_marked_for_the_save_and_counted(self):
    """When the merge of a resync answer raises at its second record, the first record is held and the replica is
    marked for the save, the answer is counted, and the peer is off the request, so the same answer is not taken
    again; the exception still leaves the handler."""
    # An oracle whose messages are only recorded.
    store = oracle("report")
    # Its first request, to both peers.
    lap(store, 0)
    # The store's copy of a value, which the merge calls first for each record: it fails for the one marked bad.
    store.deepcopy = copy_or_fail
    # An answer with a good record, then the one whose copy fails: the exception leaves the handler.
    with self.assertRaises(RuntimeError):
      # Through the payload handler, as the network delivers it.
      answer(store, "r1", "peer1", "DEEPLOY_OPERATIONS", [operation("x", 1), operation("y", 1, note="bad")])
    # The good record is held and the replica is marked for the save, although the merge did not end.
    self.assertEqual((sorted(store.storage), getattr(store, DIRTY)), ([operation("x", 1)[0]], True))
    # The request and the answer are counted; the peer that answered is off the request.
    self.assertEqual((resync_counts(store), store.pending_hsync["r1"][store.CS_PEERS]),
                     ({OPS_BUCKET + "/resync/asked": 1, OPS_BUCKET + "/resync/answered": 1}, ["oracle"]))

  def test_a_merge_that_raises_after_removing_a_revoked_payload_leaves_the_replica_marked_for_the_save(self):
    """A caller's merge that removes the payload of a revoked secret revision and then raises at a later copy has
    marked the replica for the save: the removal is a change like a stored copy."""
    # The payload of revision 1 of job 7, its revocation marker, and another field of the namespace.
    payload, marker, other = (_hset_key("DAUTH_JOB_SECRET_REVISIONS", field) for field in ("7|1", "7|1|revoked", "8|1"))
    # A dAuth member that holds the marker and, still, the payload it revokes.
    store = oracle("report", storage={payload: record(payload, {"secret": "s"}, "peer1"),
                                      marker: record(marker, {"job_id": "7", "revision": 1}, "peer1")})
    # The copy of a value fails for the one marked bad.
    store.deepcopy = copy_or_fail
    # An answer with the payload as a peer still holds it, then a copy at which the merge raises.
    with self.assertRaises(RuntimeError):
      # The merge a caller's sync runs.
      getattr(store, MERGE)("DAUTH_JOB_SECRET_REVISIONS", {
        payload: record(payload, {"secret": "s"}, "peer1"), other: record(other, {"note": "bad"}, "peer1")})
    # The revoked payload is gone, and the replica is marked for the save.
    self.assertEqual((sorted(store.storage), getattr(store, DIRTY)), ([marker], True))

  def test_only_values_that_carry_the_signed_field_are_taken(self):
    """From a resync answer of DEEPLOY_DEPLOYED_JOBS a pointer signed with a revision is merged; an old-style
    pointer without one is not brought back, and in 'on' the tombstone of a delete does not remove a pointer held."""
    # The pointers' keys: job 7 (signed), job 8 and job 9 (old style, a plain CID).
    job7, job8, job9 = (_hset_key("DEEPLOY_DEPLOYED_JOBS", name) for name in ("7", "8", "9"))
    # Each mode in which a sync answer can carry a tombstone or not.
    for mode in ("report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A network that delivers at once.
        network = _FakeNetwork()
        # "peer1" holds the signed pointer of job 7, the old-style one of job 8 and the tombstone of job 9's delete.
        oracle(mode, "peer1", ("node",), {
          job7: record(job7, SIGNED_POINTER, "oracle"), job8: record(job8, "cid-8", "oracle"),
          "dl:" + job9: record(job9, None, "oracle", [NOW_MS, "oracle"]),
        }, network)
        # The node holds only the old-style pointer of job 9: job 8's was removed here.
        store = oracle(mode, peers=("peer1",), storage={job9: record(job9, "cid-9", "oracle")}, network=network)
        # The third namespace's turn.
        setattr(store, TURN, 2)
        # Its request, answered within the lap.
        self.assertEqual([body["hkey"] for _, body in lap(store, 0) if body["op"] == "SHSYNC_REQ"],
                         ["DEEPLOY_DEPLOYED_JOBS"])
        # Job 7 arrived; job 8 did not come back; job 9 is still held.
        self.assertEqual(sorted(store.storage), sorted([job7, job9]))

  def test_the_key_rotations_which_carry_no_signed_field_are_merged_under_their_own_rule(self):
    """A resync answer of NODE_KEY_ROTATIONS, whose values have no sequence or revision, is not filtered by a signed
    field: a rotation whose proof the block engine accepts is merged, one it refuses is not."""
    # The fields of two replaced keys.
    good, bad = (_hset_key("NODE_KEY_ROTATIONS", name) for name in ("old1", "old2"))
    # A network that delivers at once.
    network = _FakeNetwork()
    # "peer1", a dAuth member, holds a rotation with a proof and one whose proof is missing.
    oracle("report", "peer1", ("node",), {
      good: record(good, {"proof": ["old1", "new1"]}, "peer1"), bad: record(bad, {"proof": None}, "peer1"),
    }, network)
    # The node holds neither.
    store = oracle("report", peers=("peer1",), network=network)
    # Its block engine's check of a proof: the old and the new key when there is one, an exception otherwise.
    store.bc.verify_node_key_rotation = lambda value: tuple(value["proof"])
    # The fifth namespace's turn.
    setattr(store, TURN, 4)
    # Its request, answered within the lap.
    self.assertEqual([body["hkey"] for _, body in lap(store, 0) if body["op"] == "SHSYNC_REQ"], ["NODE_KEY_ROTATIONS"])
    # Only the rotation with a valid proof was merged.
    self.assertEqual(sorted(store.storage), [good])


class TestMergeOfTheValueHeld(unittest.TestCase):
  """A sync copy whose value is the one held is not checked by the signed-record rule again."""

  def test_a_copy_of_the_value_held_is_not_verified_again_and_another_value_still_is(self):
    """Merging an answer with one operation already held and one new verifies one signature, not two, and merges
    the new one; a copy with another value and a signature that does not verify is still refused, and so is the
    delete copy of an operation not held (no value is never "the value held")."""
    # Each mode: the rule is the same.
    for mode in ("off", "report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A replica holding operation x.
        store = oracle(mode, storage=dict([operation("x", 1)]))
        # The signature checks made, by the value checked.
        checked = []
        # The block engine's check, recorded; "valid" verifies as before.
        store.bc.verify = lambda value, **kwargs: checked.append(value["operation_id"]) or SimpleNamespace(
          valid=value.get("EE_SIGN") == "valid")
        # An answer with x as held and a new operation y: only y is merged.
        self.assertEqual(getattr(store, MERGE)("DEEPLOY_OPERATIONS", dict([operation("x", 1), operation("y", 1)])), 1)
        # Only y's signature was checked.
        self.assertEqual(checked, ["y"])
        # x at the same sequence with other content and a signature that does not verify.
        key, forged = operation("x", 1, note="b")
        # Its signature.
        forged["value"]["EE_SIGN"] = "forged"
        # Not merged: the value held stays.
        self.assertEqual((getattr(store, MERGE)("DEEPLOY_OPERATIONS", {key: forged}),
                          store.storage[key]["value"]["note"]), (0, "a"))
        # The stamped delete copy of an operation this replica does not hold.
        gone = operation("z", 1)[0]
        # Refused by the signed-record rule as before: nothing merged and no tombstone left.
        self.assertEqual((getattr(store, MERGE)("DEEPLOY_OPERATIONS", {gone: record(gone, None, "oracle",
                                                                                     [NOW_MS, "oracle"])}),
                          "dl:" + gone in store.storage), (0, False))

  def test_a_copy_equal_to_the_value_held_is_checked_before_anything_of_it_is_stored(self):
    """A copy whose value Python holds equal to the one held but whose text differs (5.0 for 5), so that its
    signature fails, is refused when it would change the held record's token, in every mode; the same copy without
    the token, and an unstamped copy as a node of the previous release sends it, change nothing but the stamp and
    cost no signature check; the held value stays the signed one."""
    # Each mode: the rule has no switch.
    for mode in ("off", "report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # Operation x as a replica holds it.
        key, held = operation("x", 1)
        # Its value signed over its text, with a whole number in it.
        held["value"] = text_signed({**held["value"], "updated_at": 5})
        if mode != "off":
          # Stamped by its coordinator ('off' keeps no stamp).
          held["ver"] = [NOW_MS, "oracle"]
        # The replica.
        store = oracle(mode, storage={key: held})
        # The signature checks made.
        checked = []
        # A block engine that checks the signature over the value's text, and records each check.
        store.bc.verify = lambda value, **kwargs: checked.append(1) or SimpleNamespace(valid=value == text_signed(value))
        # The same value with 5.0 for 5: equal for Python, another text, so the signature does not cover it.
        variant = {**held["value"], "updated_at": 5.0}
        # Equal, and not verifying.
        self.assertEqual((variant == held["value"], store.bc.verify(variant).valid), (True, False))
        # Only the merge's checks are counted from here.
        checked.clear()
        # A copy of the variant with a token and a newer stamp: it would be stored (the token differs), so it is
        # checked first, fails, and is skipped.
        self.assertEqual(getattr(store, MERGE)("DEEPLOY_OPERATIONS", {key: {
          **held, "value": variant, "token": "T", "ver": [NOW_MS + 1000, "peer1"]}}), 0)
        # One signature check; the record held is untouched, stamp included.
        self.assertEqual((len(checked), store.storage[key]), (1, held))
        # The variant without the token, with a newer stamp: it leaves everything but the stamp, and is not checked.
        self.assertEqual(getattr(store, MERGE)("DEEPLOY_OPERATIONS", {key: {
          **held, "value": variant, "ver": [NOW_MS + 2000, "peer1"]}}), 0)
        # An unstamped copy of the value held, as a node of the previous release answers: not checked either.
        self.assertEqual(getattr(store, MERGE)("DEEPLOY_OPERATIONS", {key: {
          name: item for name, item in held.items() if name != "ver"}}), 0)
        # Still one check in all.
        self.assertEqual(len(checked), 1)
        # The record now.
        now = store.storage[key]
        # The value is the one held before (a whole number, its signature valid), with no token; only the stamp
        # was raised, where stamps are read.
        self.assertEqual((type(now["value"]["updated_at"]), store.bc.verify(now["value"]).valid, now["token"],
                          now.get("ver")), (int, True, None, None if mode == "off" else [NOW_MS + 2000, "peer1"]))

  def test_an_equal_copy_of_a_held_value_the_rule_refuses_is_skipped_and_raises_nothing(self):
    """A record of an operation namespace held from before the signed-record rule, a plain text or a dict without
    the signed fields: a copy equal to it is skipped as any copy the rule refuses, without an exception, and the
    record keeps its owner and readonly mark, in every mode."""
    # Each mode, each of the two operation namespaces, each old form of a value.
    for mode, namespace, value in itertools.product(
        ("off", "report", "on"), ("DEEPLOY_OPERATIONS", "DEEPLOY_NODE_OPERATIONS"), ("legacy-text", {"note": "old"})):
      # One sub-test each.
      with self.subTest(mode=mode, namespace=namespace, value=value):
        # The field of the old record.
        key = _hset_key(namespace, "legacy")
        # As a replica holds it from before the rule: another owner than the rule derives, not readonly.
        held = record(key, value, "peer2")
        # The replica.
        store = oracle(mode, storage={key: held})
        # A copy of exactly that record: nothing merged, nothing raised.
        self.assertEqual(getattr(store, MERGE)(namespace, {key: copy.deepcopy(held)}), 0)
        # The record is as it was.
        self.assertEqual(store.storage[key], held)

  def test_a_held_record_whose_coordinator_left_the_registry_takes_no_token_from_a_copy(self):
    """A held operation whose coordinator is no longer in the oracle registry fails the signed-record rule today:
    a copy of its value that carries a token and a newer stamp gives it neither, in every mode."""
    # Each mode.
    for mode in ("off", "report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # Operation x as a replica holds it.
        key, held = operation("x", 1)
        if mode != "off":
          # Stamped by its coordinator ('off' keeps no stamp).
          held["ver"] = [NOW_MS, "oracle"]
        # The replica.
        store = oracle(mode, storage={key: held})
        # The coordinator "oracle" has left the oracle registry.
        store.bc.get_eth_oracles = lambda: []
        # A copy of the value held, with a token and a newer stamp: checked before it is stored, and skipped.
        self.assertEqual(getattr(store, MERGE)("DEEPLOY_OPERATIONS", {key: {
          **held, "token": "T", "ver": [NOW_MS + 1000, "peer1"]}}), 0)
        # Neither the token nor the stamp was taken.
        self.assertEqual(store.storage[key], held)


class TestMergeRegistryReads(unittest.TestCase):
  """One merge reads each registry at most once, however many signed copies it checks, also when the read fails."""

  def test_a_merge_reads_each_registry_once_and_does_not_repeat_a_read_that_failed(self):
    """A merge of 20 signed operations the replica lacks reads the dAuth and the oracle registry once each, not
    once per operation, and merges them all; the next merge reads them again; with a registry read that fails,
    the merge makes that read once, not 20 times, and merges nothing; and a question for dAuth members only is
    not answered from the oracle registry the same merge has read."""
    # Twenty signed operations of the coordinator "oracle", which the replica lacks.
    answer = dict(operation("op%d" % index, 1) for index in range(20))
    # A replica of a dAuth member.
    store = oracle("report")
    # The registry reads made, by registry.
    reads = []
    # The dAuth registry, each read recorded.
    store.bc.get_eth_dauth_oracles = lambda: reads.append("dauth") or ["node", "peer1"]
    # The oracle registry, each read recorded.
    store.bc.get_eth_oracles = lambda: reads.append("oracles") or ["oracle"]
    # All twenty are merged, on one read of each registry.
    self.assertEqual((getattr(store, MERGE)("DEEPLOY_OPERATIONS", answer), reads), (20, ["dauth", "oracles"]))
    # A second merge, of twenty others, reads them again: nothing read for one merge is kept for the next.
    self.assertEqual((getattr(store, MERGE)("DEEPLOY_OPERATIONS", dict(operation("more%d" % index, 1)
                                                                       for index in range(20))), reads),
                     (20, ["dauth", "oracles"] * 2))
    # The first twenty again, each with a token: copies of the values held that would be stored, so each is put
    # through the rule before its store. They are stored on one more read of each registry.
    self.assertEqual((getattr(store, MERGE)("DEEPLOY_OPERATIONS", {key: {**held, "token": "T"}
                                                                   for key, held in answer.items()}), reads),
                     (20, ["dauth", "oracles"] * 3))
    # A replica whose oracle registry cannot be read (the remote call fails once the engine's copy has expired).
    store = oracle("report")
    # The failing reads made.
    failed = []

    def failing():
      """
      Stand for a registry read whose remote call fails: record the call, then raise as the block engine does.

      Returns
      -------
      None
        Never returns.

      Raises
      ------
      RuntimeError
        Always.

      Examples
      --------
      >>> failing()
      Traceback (most recent call last):
      RuntimeError: registry call failed
      """
      # One more remote call made.
      failed.append(1)
      # As the block engine lets the remote call's error out.
      raise RuntimeError("registry call failed")

    # The oracle registry's read fails.
    store.bc.get_eth_oracles = failing
    # Nothing is merged (no coordinator can be found in a registry), on one failing read for the twenty copies.
    self.assertEqual((getattr(store, MERGE)("DEEPLOY_OPERATIONS", answer), len(failed), store.storage), (0, 1, {}))
    # Two current secrets of jobs, signed by the coordinator "oracle": one published by the dAuth member "peer1",
    # one by "oracle2", which is in the oracle registry only.
    secrets = {}
    # Each job and its publisher.
    for job, author in (("1", "peer1"), ("2", "oracle2")):
      # The field of the job's secrets.
      key = _hset_key("DAUTH_JOB_SECRETS", job)
      # Its versioned record, published (EE_SENDER) by that author.
      secrets[key] = record(key, {"job_id": job, "deployment_revision": 1, "operation_id": "op", "coordinator": "oracle",
                                  "EE_SENDER": author, "EE_SIGN": "valid"}, "oracle")
    # A dAuth member's replica whose oracle registry also holds "oracle2".
    store = oracle("report")
    # The reads of this merge.
    reads.clear()
    # The dAuth registry, each read recorded.
    store.bc.get_eth_dauth_oracles = lambda: reads.append("dauth") or ["node", "peer1"]
    # The two oracles, each read recorded.
    store.bc.get_eth_oracles = lambda: reads.append("oracles") or ["oracle", "oracle2"]
    # Only the record a dAuth member published is merged: the second one's publisher is asked for among dAuth
    # members only, although the merge has read the oracle registry for the first record's coordinator.
    self.assertEqual((getattr(store, MERGE)("DAUTH_JOB_SECRETS", secrets), sorted(store.storage)),
                     (1, [_hset_key("DAUTH_JOB_SECRETS", "1")]))
    # One read of each registry for the merge's own membership check and both records' publishers and coordinator.
    self.assertEqual(reads, ["dauth", "oracles"])


class TestResyncStart(unittest.TestCase):
  """on_init leaves the resync ready for the first lap that finds a peer."""

  def setUp(self):
    """Give the test an empty data folder and a SerializationManager that takes the registration."""
    # A data folder removed after the test.
    folder = tempfile.TemporaryDirectory()
    # Removed after the test.
    self.addCleanup(folder.cleanup)
    # The folder on_init looks for the replica in.
    self.data_folder = folder.name
    # Its cache folder, as the plugin's persistence folder names it.
    os.makedirs(os.path.join(self.data_folder, "chain_store"))
    # The manager in use before the test, put back after it.
    previous = SerializationManager.instance
    # A manager that takes the registration and does nothing.
    SerializationManager.instance = SimpleNamespace(register=lambda save, every: None)
    # Put back after the test.
    self.addCleanup(setattr, SerializationManager, "instance", previous)

  def test_a_started_oracle_asks_at_the_first_lap_that_finds_a_peer(self):
    """After on_init the first process() lap whose peer refresh finds a registry member sends the first request."""
    # A store started by the real on_init, with the signed store's registry and a whitelist of one dAuth member.
    store = _StartHarness(self.data_folder)
    # The registry of the signed store, and the whitelist the peer refresh reads.
    store.bc = SimpleNamespace(
      node_address_to_eth_address=lambda address: address, get_eth_dauth_oracles=lambda: ["node", "peer1"],
      get_eth_oracles=lambda: ["oracle"], get_whitelist=_WhitelistBlockEngine(["node", "peer1"]).get_whitelist,
    )
    # NetMon reports no peer running yet.
    store.netmon = _SummaryControlNetmon(online_for_control=set())
    # The start: its refresh finds no peer.
    store.on_init()
    # A first lap, 61 s later (a refresh is due): still no peer, nothing asked.
    store._now = 61
    # That lap.
    store.process()
    # Nothing sent.
    self.assertEqual(store.sent_payloads, [])
    # The peer is reported running; the next refresh, a minute later, finds it.
    store.netmon = _SummaryControlNetmon(online_for_control={"peer1"})
    # The lap of that refresh.
    store._now = 122
    # It asks.
    store.process()
    # The first namespace, of that peer.
    self.assertEqual([(sent["node_addr"], sent["data"]["CHAIN_STORE_DATA"]["hkey"]) for sent in store.sent_payloads],
                     [(["peer1"], "DEEPLOY_OPERATIONS")])


if __name__ == "__main__":
  # Run this module alone.
  unittest.main()
