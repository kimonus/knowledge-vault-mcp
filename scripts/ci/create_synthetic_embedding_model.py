"""Create a tiny random 384-dimensional model without fetching model weights."""

from pathlib import Path

from sentence_transformers import SentenceTransformer, models
from tokenizers import Tokenizer
from tokenizers.models import WordPiece
from tokenizers.pre_tokenizers import Whitespace
from transformers import BertConfig, BertModel, PreTrainedTokenizerFast

root = Path("/fixture")
raw = root / "raw-model"
output = root / "model"
raw.mkdir()
output.mkdir()
vocab = {
    word: i
    for i, word in enumerate(
        ("[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "fixture", "keyboard", "router")
    )
}
tokenizer = Tokenizer(WordPiece(vocab=vocab, unk_token="[UNK]"))  # noqa: S106
tokenizer.pre_tokenizer = Whitespace()
fast = PreTrainedTokenizerFast(
    tokenizer_object=tokenizer,
    unk_token="[UNK]",  # noqa: S106 -- tokenizer vocabulary markers, not credentials
    pad_token="[PAD]",  # noqa: S106
    cls_token="[CLS]",  # noqa: S106
    sep_token="[SEP]",  # noqa: S106
    mask_token="[MASK]",  # noqa: S106
)
fast.save_pretrained(raw)
config = BertConfig(
    vocab_size=len(vocab),
    hidden_size=384,
    num_hidden_layers=1,
    num_attention_heads=6,
    intermediate_size=512,
    max_position_embeddings=128,
)
BertModel(config).save_pretrained(raw)
transformer = models.Transformer(
    str(raw), model_args={"local_files_only": True}, tokenizer_args={"local_files_only": True}
)
pooling = models.Pooling(384)
SentenceTransformer(modules=[transformer, pooling]).save(str(output))
print("Created random synthetic model; no external weights were used")
