"""Synthetic checks for ehr_evidence_app.generation.worker.

The worker is served in-process with its stand-in engine, and its device
map is built for a stand-in module tree; no model or GPU library is
loaded.
"""

import json
import unittest

from ehr_evidence_app.generation import shape, worker


class Module:
    """A stand-in module tree with the traversal the device map uses."""

    def __init__(self, **children):
        """Hold named children."""
        self.children = children

    def named_children(self):
        """Yield (name, child) pairs."""
        return iter(self.children.items())

    def named_modules(self, prefix=""):
        """Yield (qualified name, module) pairs, this one first."""
        yield prefix, self
        for name, child in self.children.items():
            yield from child.named_modules(
                "%s.%s" % (prefix, name) if prefix else name)

    def get_submodule(self, path):
        """Return a descendant by its qualified name."""
        module = self
        for name in path.split("."):
            module = module.children[name]
        return module


class Language(Module):
    """A stand-in language model: layers and token embeddings."""

    layers = embed_tokens = None


def stand_in():
    """Return a stand-in of the image-text model's module tree."""
    return Module(
        model=Module(vision_tower=Module(), multi_modal_projector=Module(),
                     language_model=Language(embed_tokens=Module(),
                                             layers=Module(), norm=Module(),
                                             rotary_emb=Module())),
        lm_head=Module())


class WorkerTests(unittest.TestCase):
    """The placement and the protocol, without a model."""

    def test_placement(self):
        """The language layers, norm and rotary embeddings go on the GPU;
        the token embeddings, image encoder, projector and output layer
        stay in system memory."""
        self.assertEqual(worker.placement(stand_in()), {
            "model.language_model.embed_tokens": "cpu",
            "model.language_model.layers": 0,
            "model.language_model.norm": 0,
            "model.language_model.rotary_emb": 0,
            "model.vision_tower": "cpu",
            "model.multi_modal_projector": "cpu",
            "lm_head": "cpu"})

    def test_serve(self):
        """Each request gets one reply with its id; a malformed line and an
        unknown operation are failures; stop ends the loop."""
        replies = []
        lines = [json.dumps({"op": "count", "text": "abcd" * 3, "id": 1}),
                 json.dumps({"op": "count", "messages": [
                     {"role": "user", "content": "x" * 8}], "id": 2}),
                 json.dumps({"op": "generate", "messages": [
                     {"role": "user", "content": "x"}], "schema": {},
                     "max_tokens": 5, "id": 3}),
                 "not json",
                 json.dumps({"op": "unknown", "id": 4}),
                 json.dumps({"op": "stop", "id": 5}),
                 json.dumps({"op": "count", "text": "never", "id": 6})]
        worker.serve(worker.FakeEngine("ok"), lines, replies.append)
        self.assertEqual([reply.get("id") for reply in replies],
                         [1, 2, 3, None, 4, 5])
        self.assertEqual(replies[0]["tokens"], 4)
        self.assertEqual(replies[2]["text"], "{}")
        self.assertEqual(replies[3]["error"], "backend_failed")
        self.assertEqual(replies[4]["error"], "backend_failed")
        self.assertEqual(replies[5]["op"], "stopped")

    def test_failures_are_replies(self):
        """Exhausted memory and other failures are replies, with the
        exception's type only."""
        for behaviour, reason in (("oom", "resource_exhausted"),
                                  ("fail", "backend_failed")):
            replies = []
            worker.serve(worker.FakeEngine(behaviour), [json.dumps({
                "op": "generate", "messages": [
                    {"role": "user", "content": "x"}], "schema": {},
                "max_tokens": 5, "id": 1})], replies.append)
            self.assertEqual(replies[0]["error"], reason)
            self.assertEqual(set(replies[0]), {"error", "type", "id"})

    def test_quote_tokens(self):
        """The runaway guard knows every token that holds a double quote,
        written or as a byte."""
        class Tokenizer:
            """A stand-in vocabulary."""

            @staticmethod
            def get_vocab():
                """Return tokens by id."""
                return {'"': 1, '",': 2, "<0x22>": 3, "word": 4, "{": 5}

        self.assertEqual(worker.quote_tokens(Tokenizer()), {1, 2, 3})

    def test_separators(self):
        """The runtime writes JSON as the prompts' examples do."""
        self.assertEqual(worker.SEPARATORS, shape.SEPARATORS)

    def test_out_of_memory(self):
        """Memory errors and CUDA's are recognised by type or message."""
        self.assertTrue(worker.out_of_memory(MemoryError()))
        self.assertTrue(worker.out_of_memory(RuntimeError(
            "CUDA out of memory. Tried to allocate")))
        self.assertFalse(worker.out_of_memory(ValueError("bad value")))


if __name__ == "__main__":
    unittest.main()
