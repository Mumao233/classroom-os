import pytest

from classroom_web.domain.auth import AuthRejected
from classroom_web.domain.capture import missing_ranges, sequence_ranges, validate_chunk


def test_ranges_distinguish_durable_holes_and_unknown_final_boundary():
    assert sequence_ranges([3,0,1,3,7]) == [dict(first_seq=0,last_seq=1),dict(first_seq=3,last_seq=3),dict(first_seq=7,last_seq=7)]
    assert missing_ranges([0,1,3],5) == [dict(first_seq=2,last_seq=2),dict(first_seq=4,last_seq=5)]
    assert missing_ranges([],None) == []


@pytest.mark.parametrize('field,value',[('byte_length',3),('end_sample',0),('seq',True),('start_sample',-1),('fence_token',0)])
def test_pcm_descriptor_rejects_invalid_shape(field,value):
    body=dict(seq=0,start_sample=0,end_sample=16000,byte_length=32000,fence_token=1)
    body[field]=value
    with pytest.raises(AuthRejected):validate_chunk(body,max_samples=160000,max_chunks=10)


def test_tail_pcm_is_supported_without_padding():
    validate_chunk(dict(seq=0,start_sample=0,end_sample=257,byte_length=514,fence_token=1),max_samples=160000,max_chunks=10)
