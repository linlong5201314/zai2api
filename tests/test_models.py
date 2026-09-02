from app.models import ModelVariant, public_model_list, estimate_tokens


def test_suffix_thinking():
    v = ModelVariant("glm-4.7-thinking")
    assert v.base == "glm-4.7"
    assert v.thinking is True
    assert v.effective_thinking() is True


def test_suffix_nothinking():
    v = ModelVariant("GLM-4.5-NO THINKING".lower().replace(" ", ""))
    assert v.thinking is False


def test_suffix_search():
    v = ModelVariant("glm-4.7-search")
    assert v.search is True
    assert v.base == "glm-4.7"


def test_combined_suffixes():
    v = ModelVariant("glm-4.7-thinking-search")
    assert v.base == "glm-4.7" and v.thinking is True and v.search is True


def test_unknown_model_passthrough():
    v = ModelVariant("some-new-model")
    assert not v.known
    up = v.upstream()
    assert up["model"] == "some-new-model"


def test_vision_needs_account():
    assert ModelVariant("glm-4.6v").needs_account
    assert ModelVariant("glm-5v-turbo").vision
    assert ModelVariant("glm-5.2").needs_account
    assert not ModelVariant("glm-4.7").needs_account


def test_model_list_variants():
    ids = {m["id"] for m in public_model_list()}
    assert "glm-4.7" in ids and "glm-4.7-thinking" in ids \
        and "glm-4.7-search" in ids and "glm-4.7-nothinking" in ids


def test_estimate_tokens_cjk():
    assert estimate_tokens("你好世界") == 4
    assert estimate_tokens("hello world") == 3
