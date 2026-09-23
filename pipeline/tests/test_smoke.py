import numpy, PIL

def test_imports():
    assert numpy.zeros(1).shape == (1,)
    assert PIL.__name__ == "PIL"
