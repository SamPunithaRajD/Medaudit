import io
import zipfile

import pytest

from app.core.exceptions import UnsupportedMediaType
from app.document_intelligence.ingestion.sources import enumerate_pages, render_page
from app.document_intelligence.ingestion.validators import safe_filename, validate_upload_type
from app.document_intelligence.ingestion.zip_safety import InvalidArchive, inspect_zip
from app.utils.sample_casesheet import build_sample_pdf, build_sample_zip_of_images

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def make_zip(entries: dict[str, bytes], compression=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_type_validation():
    assert validate_upload_type("a.pdf", b"%PDF-1.7") == "pdf"
    assert validate_upload_type("a.ZIP", b"PK\x03\x04xx") == "zip"
    with pytest.raises(UnsupportedMediaType):
        validate_upload_type("a.exe", b"MZ")
    with pytest.raises(UnsupportedMediaType):
        validate_upload_type("a.pdf", b"PK\x03\x04")  # extension/signature mismatch


def test_safe_filename():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("C:\\x\\evil<>.pdf") == "evil__.pdf"


@pytest.mark.parametrize("name", ["../evil.png", "/abs.png", "C:/x.png", "a/../../b.png"])
def test_zip_path_traversal(name):
    with pytest.raises(InvalidArchive):
        inspect_zip(make_zip({name: PNG}))


def test_zip_bomb_ratio():
    with pytest.raises(InvalidArchive, match="ratio"):
        inspect_zip(make_zip({"scan.png": PNG[:8] + b"\x00" * (20 * 1024 * 1024)}))


def test_zip_rejects_nested_and_unknown_and_mismatch():
    with pytest.raises(InvalidArchive):
        inspect_zip(make_zip({"inner.zip": b"PK\x03\x04"}))
    with pytest.raises(InvalidArchive):
        inspect_zip(make_zip({"run.exe": b"MZ"}))
    with pytest.raises(InvalidArchive):
        inspect_zip(make_zip({"fake.png": b"%PDF-1.4 not png"}))
    with pytest.raises(InvalidArchive):
        inspect_zip(make_zip({"__MACOSX/._x": b"junk"}))  # nothing supported left
    with pytest.raises(InvalidArchive):
        inspect_zip(b"PK\x03\x04 corrupt")


def test_zip_valid_natural_order():
    members = inspect_zip(make_zip({"p10.png": PNG, "p2.png": PNG, "p1.pdf": build_sample_pdf()}))
    assert [m.name for m in members] == ["p1.pdf", "p2.png", "p10.png"]


def test_enumerate_pdf_and_zip():
    assert len(enumerate_pages(build_sample_pdf(), "pdf")) == 8
    refs = enumerate_pages(build_sample_zip_of_images(pages=2), "zip")
    assert [r.source_kind for r in refs] == ["image", "image"]


def test_page_limit(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.get_settings(), "max_pdf_pages", 3)
    with pytest.raises(Exception, match="maximum"):
        enumerate_pages(build_sample_pdf(), "pdf")


def test_render_pdf_text_layer_and_image():
    data = build_sample_pdf()
    ref = enumerate_pages(data, "pdf")[0]
    p = render_page(data, "pdf", ref, with_image=False)
    assert p.image is None and "EMERGENCY" in p.text_layer and p.text_words
    p2 = render_page(data, "pdf", ref, dpi=72)
    assert p2.image is not None and p2.image.shape[2] == 3
