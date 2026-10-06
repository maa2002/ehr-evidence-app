"""The model worker (work item 42).

Runs in the model environment (requirements/llm.lock.txt), never in the
application's. It loads the pinned model once and answers one request at a
time: one JSON line per request on its standard input, one per reply on
its standard output, with no network port. Hugging Face runs offline. The
application reaches it only through generation.runtime.

Requests and replies, each reply carrying its request's "id":

* {"op": "count", "messages": [...]} or {"op": "count", "text": "..."}:
  {"tokens": n}, a prompt's tokens with its chat template, or a text's;
* {"op": "generate", "messages": [...], "schema": {...}, "max_tokens": n}:
  {"text", "prompt_tokens", "output_tokens", "finished", "seconds",
  "peak_memory_bytes"}, or {"error": "resource_exhausted"} when memory
  runs out and {"error": "backend_failed"} for any other failure;
* {"op": "stop"} ends the worker.

After loading the worker writes {"op": "ready", "describe": {...}}; a
worker that cannot load writes {"op": "failed", "error": ...} and exits.

Prompts and output travel only through the pipes. The worker writes status
lines and exception type names to its standard error, never a prompt, a
document or a record, and anything a library prints goes there too.

Precision and placement: in bf16-mixed, the configured precision (ADR
0012, decision 2), the model is loaded in bfloat16 in system memory; its
language layers, final norm and rotary embeddings are moved to the GPU,
while the image encoder, which a text request never runs, and the token
embedding table, which Gemma also uses as its output layer, stay in system
memory and run there, so the model is not GPU-resident. In nf4, measured
for comparison (ADR 0013), the weights are 4-bit NF4 computing in
bfloat16, all on the GPU. Decoding is greedy and constrained by the
request's JSON Schema.

--fake runs a stand-in that loads no model, for tests of the protocol.

Usage (in the model environment):
    python -m ehr_evidence_app.generation.worker --model-dir <directory> \\
        --revision <commit> --decoder xgrammar --seed <n>
"""

from __future__ import annotations

import argparse
import json
import sys
import time

PROTOCOL_VERSION = "worker-v1"
DECODERS = ("xgrammar", "llguidance")
# bf16-mixed: bfloat16 weights, the language layers on the GPU and the
# token embeddings and output layer on the processor (not GPU-resident);
# nf4: 4-bit NF4 weights computing in bfloat16, all on the GPU.
PRECISIONS = ("bf16-mixed", "nf4")
FAKES = ("ok", "oom", "fail", "slow", "truncate")
# Output that runs this many tokens without a double quote is inside one
# string that never ends: generation stops, and the output is cut off.
RUNAWAY_TOKENS = 150
# How JSON is written between values; generation.shape writes the
# documents in its prompts' examples the same way.
SEPARATORS = (", ", ": ")


class LoadError(Exception):
    """A worker that cannot load, carrying its reason."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def out_of_memory(error):
    """Return True when an exception is the GPU or system running out of
    memory."""
    return (isinstance(error, MemoryError)
            or type(error).__name__ == "OutOfMemoryError"
            or "out of memory" in str(error).lower())


# The Engines

class FakeEngine:
    """A stand-in for tests of the protocol: tokens are counted as one per
    four characters, and a generation writes "{}" or fails as told."""

    def __init__(self, behaviour):
        """Behave as told: ok, oom, fail, slow or truncate."""
        self.behaviour = behaviour

    def describe(self):
        """Return what this engine is."""
        return {"protocol": PROTOCOL_VERSION, "engine": "fake",
                "behaviour": self.behaviour}

    @staticmethod
    def count(text):
        """Return the tokens of a text."""
        return len(text) // 4 + 1

    def count_messages(self, messages):
        """Return the tokens of a prompt."""
        return self.count("".join(message["content"]
                                  for message in messages))

    def generate(self, messages, schema, max_tokens):
        """Write "{}", or fail as told."""
        if self.behaviour == "oom":
            raise MemoryError("out of memory")
        if self.behaviour == "fail":
            raise ValueError("failed")
        if self.behaviour == "slow":
            time.sleep(5)
        return {"text": "{}", "prompt_tokens": self.count_messages(messages),
                "output_tokens": 1,
                "finished": self.behaviour != "truncate",
                "seconds": 0.0, "peak_memory_bytes": None}


def language_model_path(model):
    """Return the qualified name of the model's language model: the module
    that holds the decoder layers and the token embeddings."""
    for name, module in model.named_modules():
        if hasattr(module, "layers") and hasattr(module, "embed_tokens"):
            return name
    raise LoadError("backend_failed")


def placement(model):
    """Return the device map of ADR 0012, decision 2: the language model's
    children on the GPU except its token embeddings; everything outside
    the language model (the image encoder, the projector and the output
    layer, tied to the token embeddings) in system memory."""
    path = language_model_path(model)
    device_map = {}
    language = model.get_submodule(path)
    for name, _ in language.named_children():
        device_map["%s.%s" % (path, name)] = (
            "cpu" if name == "embed_tokens" else 0)
    parts = path.split(".")
    for depth in range(len(parts)):
        parent = model.get_submodule(".".join(parts[:depth])) if depth \
            else model
        for name, _ in parent.named_children():
            qualified = ".".join(parts[:depth] + [name])
            if qualified != ".".join(parts[:depth + 1]):
                device_map[qualified] = "cpu"
    return device_map


def quote_tokens(tokenizer):
    """Return the ids of the tokens that hold a double quote, written or
    as a byte."""
    found = set()
    for token, number in tokenizer.get_vocab().items():
        if '"' in token or token == "<0x22>":
            found.add(number)
    return frozenset(found)


def runaway_guard(torch, quotes, limit=RUNAWAY_TOKENS):
    """Return a stopping criterion that ends generation after limit
    tokens in a row without a double quote."""
    from transformers import StoppingCriteria

    class Guard(StoppingCriteria):
        """Stops a string that never ends."""

        def __init__(self):
            self.run = 0

        def __call__(self, input_ids, scores, **kwargs):
            self.run = 0 if int(input_ids[0, -1]) in quotes else self.run + 1
            return torch.full((input_ids.shape[0],), self.run >= limit,
                              dtype=torch.bool, device=input_ids.device)

    return Guard()


class XGrammar:
    """Schema-constrained decoding with xgrammar."""

    name = "xgrammar"

    def __init__(self, tokenizer, vocab_size):
        """Build the grammar compiler for the tokenizer."""
        import importlib.metadata
        import xgrammar
        self.xgrammar = xgrammar
        info = xgrammar.TokenizerInfo.from_huggingface(tokenizer,
                                                       vocab_size=vocab_size)
        self.compiler = xgrammar.GrammarCompiler(info)
        self.version = importlib.metadata.version("xgrammar")

    def processor(self, schema):
        """Return the logits processor of one request's schema."""
        compiled = self.compiler.compile_json_schema(
            json.dumps(schema), any_whitespace=False,
            separators=SEPARATORS, strict_mode=True)
        return self.xgrammar.contrib.hf.LogitsProcessor(compiled)


class Engine:
    """The pinned model in full precision, placed across the GPU and system
    memory, with its tokenizer and a schema-constrained decoder."""

    def __init__(self, model_dir, repository, revision, decoder, seed,
                 minimum_free_mib, precision="bf16-mixed"):
        """Load the model; raises LoadError when it cannot."""
        import torch
        import transformers
        from accelerate import dispatch_model
        self.torch = torch
        if not torch.cuda.is_available():
            raise LoadError("backend_failed")
        free, total = torch.cuda.mem_get_info()
        if free < minimum_free_mib * 2 ** 20:
            raise LoadError("resource_exhausted")
        started = time.monotonic()
        self.tokenizer = transformers.AutoTokenizer.from_pretrained(
            model_dir, local_files_only=True)
        if precision == "nf4":
            model = transformers.AutoModelForImageTextToText.from_pretrained(
                model_dir, dtype=torch.bfloat16, local_files_only=True,
                device_map={"": 0},
                quantization_config=transformers.BitsAndBytesConfig(
                    load_in_4bit=True, bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.bfloat16))
            model.eval()
            self.device_map = {"": 0}
            self.model = model
        else:
            model = transformers.AutoModelForImageTextToText.from_pretrained(
                model_dir, dtype=torch.bfloat16, local_files_only=True)
            model.eval()
            self.device_map = placement(model)
            self.model = dispatch_model(model, device_map=self.device_map,
                                        main_device="cpu")
        text = getattr(model.config, "text_config", model.config)
        if decoder != "xgrammar":
            raise LoadError("backend_failed")
        self.decoder = XGrammar(self.tokenizer, text.vocab_size)
        self.quotes = quote_tokens(self.tokenizer)
        self.seed = seed
        self.stops = set(self.model.generation_config.eos_token_id
                         if isinstance(self.model.generation_config
                                       .eos_token_id, list)
                         else [self.model.generation_config.eos_token_id])
        self.info = {
            "protocol": PROTOCOL_VERSION, "engine": "transformers",
            "repository": repository, "revision": revision,
            "precision": precision,
            "dtype": "bfloat16" if precision == "bf16-mixed"
            else "nf4 with bfloat16 compute",
            "device": torch.cuda.get_device_name(0),
            "gpu_total_bytes": total,
            "gpu_weights_bytes": torch.cuda.memory_allocated(),
            "load_seconds": round(time.monotonic() - started, 1),
            "gpu_modules": sorted(name or "(the whole model)"
                                  for name, device in
                                  self.device_map.items() if device != "cpu"),
            "cpu_modules": sorted(name for name, device in
                                  self.device_map.items() if device == "cpu"),
            "torch": torch.__version__, "cuda": torch.version.cuda,
            "transformers": transformers.__version__,
            "decoder": self.decoder.name,
            "decoder_version": self.decoder.version}

    def describe(self):
        """Return what this engine is and how it was loaded."""
        return dict(self.info)

    def encode(self, messages):
        """Return the model inputs of a prompt, with its chat template."""
        return self.tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt",
            return_dict=True)

    def count_messages(self, messages):
        """Return the tokens of a prompt."""
        return int(self.encode(messages)["input_ids"].shape[1])

    def count(self, text):
        """Return the tokens of a text."""
        return len(self.tokenizer(text, add_special_tokens=False)[
            "input_ids"])

    def generate(self, messages, schema, max_tokens):
        """Write one reply, greedily, constrained by its schema. The reply
        is finished only when the model ends it; output stopped at the
        token limit or by the runaway guard is not."""
        torch = self.torch
        started = time.monotonic()
        inputs = self.encode(messages)
        prompt_tokens = int(inputs["input_ids"].shape[1])
        processor = self.decoder.processor(schema)
        torch.cuda.reset_peak_memory_stats()
        torch.manual_seed(self.seed)
        with torch.inference_mode():
            output = self.model.generate(
                **inputs, max_new_tokens=max_tokens, do_sample=False,
                temperature=None, top_p=None, top_k=None,
                logits_processor=[processor],
                stopping_criteria=[runaway_guard(torch, self.quotes)])
        seconds = time.monotonic() - started
        written = output[0, prompt_tokens:].tolist()
        finished = bool(written) and written[-1] in self.stops
        return {"text": self.tokenizer.decode(written,
                                              skip_special_tokens=True),
                "prompt_tokens": prompt_tokens,
                "output_tokens": len(written), "finished": finished,
                "seconds": round(seconds, 3),
                "peak_memory_bytes": torch.cuda.max_memory_allocated()}


# The Protocol

def handle(engine, request):
    """Return the reply to one request."""
    operation = request.get("op")
    if operation == "count":
        if "messages" in request:
            return {"tokens": engine.count_messages(request["messages"])}
        return {"tokens": engine.count(request["text"])}
    if operation == "generate":
        return engine.generate(request["messages"], request["schema"],
                               int(request["max_tokens"]))
    return {"error": "backend_failed", "type": "UnknownOperation"}


def serve(engine, lines, reply):
    """Answer requests from lines until a stop request or the end."""
    for line in lines:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except ValueError:
            reply({"error": "backend_failed", "type": "MalformedRequest"})
            continue
        if request.get("op") == "stop":
            reply({"id": request.get("id"), "op": "stopped"})
            return
        try:
            answer = handle(engine, request)
        except Exception as error:  # noqa: BLE001 - every failure is a reply
            answer = {"error": "resource_exhausted" if out_of_memory(error)
                      else "backend_failed", "type": type(error).__name__}
            print("request failed: %s" % type(error).__name__,
                  file=sys.stderr, flush=True)
            empty = getattr(getattr(engine, "torch", None), "cuda", None)
            if empty is not None:
                empty.empty_cache()
        answer["id"] = request.get("id")
        reply(answer)


def main(argv=None):
    """Load the engine and serve requests over standard input and
    output."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model-dir")
    parser.add_argument("--repository", default="")
    parser.add_argument("--revision", default="")
    parser.add_argument("--decoder", choices=DECODERS, default="xgrammar")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--minimum-free-gpu-mib", type=int, default=0)
    parser.add_argument("--precision", choices=PRECISIONS,
                        default="bf16-mixed")
    parser.add_argument("--fake", choices=FAKES)
    args = parser.parse_args(argv)
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    protocol = sys.stdout
    # Anything a library prints goes to standard error, never the protocol.
    sys.stdout = sys.stderr

    def reply(message):
        """Write one reply line."""
        protocol.write(json.dumps(message, ensure_ascii=False) + "\n")
        protocol.flush()

    try:
        engine = (FakeEngine(args.fake) if args.fake else
                  Engine(args.model_dir, args.repository, args.revision,
                         args.decoder, args.seed,
                         args.minimum_free_gpu_mib, args.precision))
    except LoadError as error:
        reply({"op": "failed", "error": error.reason})
        return 1
    except Exception as error:  # noqa: BLE001 - a load failure is a reply
        reply({"op": "failed", "error": "resource_exhausted"
               if out_of_memory(error) else "backend_failed",
               "type": type(error).__name__})
        return 1
    print("worker ready", file=sys.stderr, flush=True)
    reply({"op": "ready", "describe": engine.describe()})
    serve(engine, sys.stdin, reply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
