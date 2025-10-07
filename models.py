from llama_cpp import Llama

class MockLLM:
    """Тестовая модель для случаев, когда основная модель недоступна"""
    def __call__(self, *args, **kwargs):
        return {"choices": [{"text": "Привет! Я AI-ассистент. Как дела?"}]}
