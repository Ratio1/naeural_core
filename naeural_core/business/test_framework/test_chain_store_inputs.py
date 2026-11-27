"""
Offline tests of the chain store's payload handler on messages no node of this release sends: a key, a namespace
name, a request id or a whole body of a type or content the operations cannot use. Each must end inside the
handler, dropped and counted, because an exception that leaves the handler ends the plugin's lap.
"""
# The test framework.
import unittest
# The stand-in for the network of one test: it answers a sync request with a forged answer.
from types import SimpleNamespace

# The counter attribute's private name.
from test_chain_store_accounting import STATS
# The payload field names of the test network and the composers of a namespace's key prefix and of a hash key.
from test_chain_store_hsync import _PayloadData, _hset_index, _hset_key
# The order tests' store builder, record builder and constants.
from test_chain_store_order import BUCKET, KEY, NOW_MS, make, record
# The builder of a value nested a given number of levels deep.
from test_chain_store_size_cap import nested

# Keys that are not a non-empty str, as a JSON body can carry them.
BAD_KEYS = (5, 1.5, True, None, "", ["a"], {"a": 1})
# Those among them that are not a str (an empty str names no record but can be looked up).
NOT_STR = tuple(key for key in BAD_KEYS if not isinstance(key, str))
# The body of a pending blocking sync of the namespace "players", asked of peer1.
PENDING = {"hkey": "players", "PEERS": ["peer1"], "response": None}


def own_record(sender_stamp):
  """
  Build a record this node wrote and peer1 has not confirmed yet, as a confirmation would find it.

  Parameters
  ----------
  sender_stamp : list or None
    The stamp of the node's own store (``ver`` and ``sent_ver``); None for a record of a store with the order
    switch 'off'.

  Returns
  -------
  dict
    The record of ``KEY`` with value ``"v"``, owner ``"node"``, no confirmation and peer1 as its only target.

  Examples
  --------
  >>> own_record(None)["target_peers"]
  ['peer1']
  """
  # The stored fields of a local write waiting for peer1.
  return {**record(KEY, "v", "node", sender_stamp), "confirms": 0, "min_confirms": 1, "confirmed_peers": [],
          "target_peers": ["peer1"], **({"sent_ver": sender_stamp} if sender_stamp else {})}


def handle(store, body, sender="peer1"):
  """
  Give one message body to the store's payload handler, as the network delivers a peer's message.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    The receiving store.
  body : any
    The value of the payload's ``CHAIN_STORE_DATA``: a dict of the operation's fields, or anything else a peer
    could put there.
  sender : str, optional
    The verified sender; ``"peer1"`` by default.

  Returns
  -------
  list of dict
    The bodies of the messages the store sent while it handled this one.

  Examples
  --------
  >>> handle(make("report"), {"op": "SSTORE", "key": 5, "value": "v"})
  []
  """
  # Only what this message makes the store send.
  before = len(store.sent_payloads)
  # Through the handler, with the payload fields the network processor passes.
  store.default_handler({_PayloadData.EE_SENDER: sender, _PayloadData.EE_ID: sender,
                         _PayloadData.EE_DESTINATION: [store.ee_addr], _PayloadData.EE_IS_ENCRYPTED: True,
                         "decoded": {store.CS_DATA: body}})
  # The bodies sent back.
  return [sent["data"][store.CS_DATA] for sent in store.sent_payloads[before:]]


class TestInputsNoNodeSends(unittest.TestCase):
  """A message with a field the handler cannot use is dropped inside the handler and counted, in every mode."""

  def test_a_store_or_delete_whose_key_is_not_a_non_empty_str_is_dropped_and_counted(self):
    """A store, and a delete, whose key is a number, a bool, a list, a dict, missing or empty leaves the handler
    without an exception, stores nothing, is not confirmed and counts plain/remote/refused_invalid_key; a store
    with a str key is still applied and confirmed."""
    # Each mode: the check has no switch.
    for mode in ("off", "report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A store with nothing held.
        store = make(mode)
        # Each bad key, as a write and as a delete: nothing comes back.
        sent = [handle(store, {"op": "SSTORE", "key": key, "value": value, "owner": "peer1"})
                for key in BAD_KEYS for value in ("v", None)]
        # No confirmation and no answer for any of them.
        self.assertEqual(sent, [[]] * (2 * len(BAD_KEYS)))
        # Nothing stored.
        self.assertEqual(store.storage, {})
        # One count per message, under "plain": such a key is in no namespace.
        self.assertEqual(getattr(store, STATS).get("plain/remote/refused_invalid_key"), 2 * len(BAD_KEYS))
        # A store with a proper key is applied and confirmed as before.
        self.assertEqual(len(handle(store, {"op": "SSTORE", "key": KEY, "value": "v", "owner": "peer1"})), 1)
        # Held now.
        self.assertEqual(store.storage[KEY]["value"], "v")

  def test_a_store_to_a_secret_revision_key_whose_field_does_not_decode_is_dropped_and_counted(self):
    """A store from a registry member to a dAuth member under DAUTH_JOB_SECRET_REVISIONS whose key's field part is
    base64 without its padding, is not UTF-8 once decoded, or holds a lone surrogate leaves the handler without an
    exception, stores nothing, is neither confirmed nor answered and counts <bucket>/remote/refused_invalid_key;
    a key of the namespace whose field decodes is still stored and confirmed."""
    # The prefix of the namespace's keys, which is also the counters' bucket.
    prefix = _hset_index("DAUTH_JOB_SECRET_REVISIONS")
    # Field parts the node's decoder raises for: no padding, bytes that are not UTF-8, a lone surrogate.
    bad = ("YWxwaGE", "_w==", "a\ud800")
    # Each mode: the check has no switch.
    for mode in ("off", "report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A dAuth member's store ("node"); the sender "peer1" is a dAuth member and one of its replication peers.
        store = make(mode, signed=True)
        # The stamp the stores carry: a refused stamped store is answered in 'on', so none may be answered here.
        stamp = {} if mode == "off" else {"ver": [NOW_MS, "peer1"]}
        # Each such key, as a write and as a delete: nothing comes back and nothing is raised.
        sent = [handle(store, {"op": "SSTORE", "key": prefix + field, "value": value, "owner": "peer1", **stamp})
                for field in bad for value in ({"secret": "s"}, None)]
        # No confirmation and no answer for any of them.
        self.assertEqual(sent, [[]] * (2 * len(bad)))
        # Nothing stored, and one count per store under the namespace's bucket.
        self.assertEqual((store.storage, getattr(store, STATS)),
                         ({}, {prefix + "/remote/refused_invalid_key": 2 * len(bad)}))
        # A key of the namespace whose field decodes: applied and confirmed as before.
        self.assertEqual(len(handle(store, {"op": "SSTORE", "key": _hset_key("DAUTH_JOB_SECRET_REVISIONS", "7|1"),
                                            "value": {"secret": "s"}, "owner": "peer1", **stamp})), 1)

  def test_a_confirmation_whose_key_is_not_a_str_is_dropped_and_counted(self):
    """A full confirmation, a lean one and a refusal answer whose key is a number, a bool, a list, a dict or
    missing leave the handler without an exception, settle nothing, prove nothing about the sender and count
    plain/confirm/refused_invalid_key; a confirmation of the key itself still counts."""
    # Each mode.
    for mode in ("off", "report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # The stamp of the node's own store (none in 'off').
        stamp = None if mode == "off" else [NOW_MS, "node"]
        # A store holding its own unconfirmed record.
        store = make(mode, storage={KEY: own_record(stamp)})
        # Each key that is not a str, in the three forms of a confirmation.
        for key in NOT_STR:
          # The full form (it echoes the value and the owner).
          handle(store, {"op": "SCONFIRM", "key": key, "value": "v", "owner": "node"})
          # The lean form (key and stamp).
          handle(store, {"op": "SCONFIRM", "key": key, "ver": [NOW_MS, "node"]})
          # A refusal answer.
          handle(store, {"op": "SCONFIRM", "key": key, "ver": [NOW_MS, "node"], "refused": "older"})
        # Nothing settled on the own record.
        self.assertEqual((store.storage[KEY]["confirms"], store.storage[KEY]["confirmed_peers"]), (0, []))
        # The stamped ones did not prove the sender as a peer that stamps its stores.
        self.assertEqual(store._ChainStoreBasePlugin__stamped_peers, set())
        # One count per message.
        self.assertEqual(getattr(store, STATS), {"plain/confirm/refused_invalid_key": 3 * len(NOT_STR)})
        # The confirmation of the record itself still counts.
        handle(store, {"op": "SCONFIRM", "key": KEY, "value": "v", "owner": "node"})
        # Confirmed by peer1.
        self.assertEqual((store.storage[KEY]["confirms"], store.storage[KEY]["confirmed_peers"]), (1, ["peer1"]))

  def test_a_sync_request_for_a_namespace_name_utf8_cannot_encode_is_dropped_and_counted(self):
    """A namespace-sync request whose hkey holds a lone surrogate (a high or a low one, anywhere in the name) is
    not answered and counts plain/hsync/refused_invalid_key instead of raising at the hash of the name; names
    with other characters outside ASCII, the code points next to the surrogates included, are still answered."""
    # Each mode.
    for mode in ("off", "report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A store to ask.
        store = make(mode)
        # Names holding a lone high surrogate, a lone low one, the first and the last surrogate code point.
        bad = ("a\ud800", "\udc00b", "x\ud800y", "\udfff", "\udbffé")
        # Each asked for: no answer.
        self.assertEqual([handle(store, {"op": "SHSYNC_REQ", "request_id": "q", "hkey": hkey}) for hkey in bad],
                         [[]] * len(bad))
        # One count per request, and nothing else counted.
        self.assertEqual(getattr(store, STATS), {"plain/hsync/refused_invalid_key": len(bad)})
        # Names UTF-8 encodes: accents, a character of four bytes, and the code points just outside the surrogates.
        good = ("jucători", "\U0001f600", "퟿", "", "players")
        # Each is answered with a snapshot (an empty one: nothing is held).
        self.assertEqual([[body["op"] for body in handle(store, {"op": "SHSYNC_REQ", "request_id": "q", "hkey": hkey})]
                          for hkey in good], [["SHSYNC_RESP"]] * len(good))

  def test_a_sync_answer_whose_request_id_is_not_a_str_is_dropped_and_counted(self):
    """A namespace-sync answer whose request id is a number, a bool, a list, a dict or missing leaves the handler
    without an exception, touches no pending request and counts plain/hsync/refused_malformed; the answer with the
    pending request's id is still taken."""
    # Each mode.
    for mode in ("off", "report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A store with one blocking sync pending, asked of peer1.
        store = make(mode)
        # The pending request "r".
        store.pending_hsync["r"] = dict(PENDING)
        # An answer under each id that is not a str.
        for request_id in NOT_STR:
          # Nothing is raised.
          handle(store, {"op": "SHSYNC_RESP", "request_id": request_id, "hkey": "players", "snapshot": {}})
        # The pending request is as it was, and each answer was counted.
        self.assertEqual((store.pending_hsync, getattr(store, STATS)),
                         ({"r": PENDING}, {"plain/hsync/refused_malformed": len(NOT_STR)}))
        # The answer under the request's own id is recorded for the waiting caller.
        handle(store, {"op": "SHSYNC_RESP", "request_id": "r", "hkey": "players", "snapshot": {}})
        # Taken from peer1.
        self.assertEqual(store.pending_hsync["r"]["response"]["sender"], "peer1")

  def test_a_body_that_is_not_a_dict_is_dropped_and_counted(self):
    """A payload whose CHAIN_STORE_DATA is a list, a str, a number, a bool or null leaves the handler without an
    exception, runs no operation and counts plain/remote/refused_malformed; an empty dict names no operation and
    counts nothing, as before."""
    # Each mode.
    for mode in ("off", "report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A store with nothing held.
        store = make(mode)
        # Bodies that are valid JSON but not a dict.
        bodies = (["SSTORE"], "SSTORE", 5, True, None)
        # Each handled: nothing is sent.
        self.assertEqual([handle(store, body) for body in bodies], [[]] * len(bodies))
        # A body without an operation: ignored, uncounted.
        self.assertEqual(handle(store, {}), [])
        # One count per body that is not a dict; nothing stored.
        self.assertEqual((getattr(store, STATS), store.storage), ({"plain/remote/refused_malformed": len(bodies)}, {}))

  def test_a_blocking_sync_takes_an_answer_that_holds_values_too_deep_to_copy(self):
    """The answer to a caller's blocking sync is recorded as decoded, with no copy of it: one that holds values
    nested 65, 600 and 5,000 levels leaves the handler without an exception (a deep copy of it raised
    RecursionError there from about 445 levels), the caller's merge stores its good record and skips the deep ones
    as too_deep, the answer is left as it was received, and the stored record does not share its value with it."""
    # The good record's key and the deep ones', each under its own field of the namespace.
    good, deep = _hset_key("players", "good"), {_hset_key("players", "deep%d" % depth): depth for depth in (65, 600, 5000)}
    # Each mode.
    for mode in ("off", "report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # The stamp a copy carries (none is read in 'off').
        stamp = None if mode == "off" else [NOW_MS, "peer1"]

        def forged():
          """
          Build the forged answer: one good record and three whose values are nested too deep.

          Returns
          -------
          dict
            The snapshot, composed key to record, built anew at each call so two of them share nothing.
          """
          # The good record, and one record per depth.
          return {good: record(good, {"a": [1, 2]}, "peer1", stamp),
                  **{key: record(key, nested(depth), "peer1", stamp) for key, depth in deep.items()}}

        # The store that asks, and the answer its peer sends.
        store, answer = make(mode), forged()
        # The message as a node receives it: decoded, not copied again (the harness's own copy stops near 500 levels).
        store.receive_and_decrypt_payload = lambda data: data["decoded"]
        # A network whose peer answers the sync request at once with the forged answer, under the request's id.
        store.network = SimpleNamespace(dispatch=lambda sender, targets, decoded: handle(store, {
          "op": "SHSYNC_RESP", "request_id": decoded[store.CS_DATA]["request_id"], "hkey": "players",
          "snapshot": answer}))
        # The caller's blocking sync: the handler records the answer and the caller merges it; nothing is raised.
        result = store._hsync("players", peers=["peer1"], include_default_peers=False)
        # The good record merged, from the peer asked.
        self.assertEqual((result["source_peer"], result["merged_fields"]), ("peer1", 1))
        # Only the good record is held; each deep copy was skipped under the depth word.
        self.assertEqual((sorted(store.storage), getattr(store, STATS).get(BUCKET + "/hsync/refused_too_deep")),
                         ([good], 3))
        # The answer is as it was received: the merge only read it.
        self.assertEqual(answer, forged())
        # The stored value is a copy of its own, made where it was stored.
        self.assertEqual((store.storage[good]["value"], store.storage[good]["value"] is answer[good]["value"]),
                         ({"a": [1, 2]}, False))


if __name__ == "__main__":
  # Run this module alone.
  unittest.main()
