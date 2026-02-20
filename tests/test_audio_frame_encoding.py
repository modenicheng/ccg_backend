from icecream import ic

from utils.dataframe import AudioFrame, AudioEncoding


def test_audio_frame_encoding_decoding():
    # Create an AudioFrame instance
    original_frame = AudioFrame(
        sample_rate=44100,
        sample_num=1024,
        channels=2,
        encoding=AudioEncoding.OPUS,
        data=b'\x01\x02\x03\x04'  # Example audio data
    )

    # Encode the frame to bytes
    encoded_data = original_frame.bin

    # Create a new AudioFrame instance and load the encoded data
    decoded_frame = AudioFrame.load(encoded_data)

    # Assert that the original and decoded frames are the same
    assert original_frame.event_type == decoded_frame.event_type
    assert original_frame.timestamp == decoded_frame.timestamp
    assert original_frame.sample_rate == decoded_frame.sample_rate
    assert original_frame.sample_num == decoded_frame.sample_num
    assert original_frame.channels == decoded_frame.channels
    assert original_frame.encoding == decoded_frame.encoding
    assert original_frame.data == decoded_frame.data
    ic(decoded_frame.to_dict())


test_audio_frame_encoding_decoding()
