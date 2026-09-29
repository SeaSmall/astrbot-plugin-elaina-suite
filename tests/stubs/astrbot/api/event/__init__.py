class AstrMessageEvent:
    """桩：仅用于类型标注与测试。"""
    pass


class MessageChain:
    def __init__(self, chain=None):
        self.chain = chain or []

    @classmethod
    def message(cls, text):
        return cls(chain=[{"type": "text", "text": text}])

    @classmethod
    def url_image(cls, url):
        return cls(chain=[{"type": "image", "url": url}])