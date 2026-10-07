"""Explicitly prefetch pinned test/runtime model files; never called by requests."""

import argparse

from huggingface_hub import snapshot_download

from tracecause.investigation.generation import PINNED_TOKENIZER_REVISION
from tracecause.retrieval.embeddings import MODEL_REVISION


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--embeddings", action="store_true")
    args = parser.parse_args()
    patterns = [
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "vocab.txt",
    ]
    snapshot_download(
        "Qwen/Qwen3-4B-Instruct-2507", revision=PINNED_TOKENIZER_REVISION, allow_patterns=patterns
    )
    if args.embeddings:
        from tracecause.retrieval.embeddings import MiniLMEmbedder

        snapshot_download(
            "sentence-transformers/all-MiniLM-L6-v2",
            revision=MODEL_REVISION,
            allow_patterns=["*.json", "*.txt", "*.safetensors", "1_Pooling/*"],
        )
        MiniLMEmbedder()
    print("Pinned tokenizer prefetch complete; embedding weights requested:", args.embeddings)


if __name__ == "__main__":
    main()
