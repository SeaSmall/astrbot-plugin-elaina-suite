class Image:
    def __init__(self, *a, **k):
        pass

    @classmethod
    def fromBytes(cls, data):
        return cls()


class Plain:
    def __init__(self, *a, **k):
        self.text = ""


class File:
    def __init__(self, *a, **k):
        pass