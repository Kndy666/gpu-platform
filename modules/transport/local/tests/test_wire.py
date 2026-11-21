from io import BytesIO

import pytest

from gpu_platform_transport_local.wire import MAX_MESSAGE_BYTES, encode_message, read_message


@pytest.mark.parametrize("value", [{}, {"parameters": {"path": "a/b", "size": 1024}}, {"ok": False}])
def test_message_roundtrip(value):
    assert read_message(BytesIO(encode_message(value))) == value


@pytest.mark.parametrize("frame", [b"{}", b"[]\n", b"null\n", b"{\n", b"x" * (MAX_MESSAGE_BYTES + 1)])
def test_invalid_frames_are_rejected(frame):
    with pytest.raises(ValueError):
        read_message(BytesIO(frame))


def test_output_frame_is_bounded():
    with pytest.raises(ValueError):
        encode_message({"payload": "x" * MAX_MESSAGE_BYTES})


def test_read_consumes_one_frame():
    stream = BytesIO(encode_message({"sequence": 1}) + encode_message({"sequence": 2}))
    assert read_message(stream)["sequence"] == 1
    assert read_message(stream)["sequence"] == 2
