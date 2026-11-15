"""Keep LangChain batching/validation while recording provider-reported usage."""

from langchain_openai import OpenAIEmbeddings

from .observe import count, observation, record_usage


class _ObservedClient:
    def __init__(self, client, model):
        self.client, self.model = client, model

    def create(self, **kwargs):
        with observation(
            "embedding.provider_request", as_type="embedding", model=self.model
        ) as span:
            count("embedding_provider_requests")
            response = self.client.create(**kwargs)
            usage = (
                response.get("usage")
                if isinstance(response, dict)
                else getattr(response, "usage", None)
            )
            record_usage(span, usage)
            return response

    def __getattr__(self, name):
        return getattr(self.client, name)


def build_embeddings(settings):
    model = OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.embedding_api_key.get_secret_value(),
        base_url=settings.embedding_base_url,
        dimensions=settings.embedding_dimensions,
        request_timeout=settings.model_timeout,
        max_retries=2,
        check_embedding_ctx_length=False,
    )
    model.client = _ObservedClient(model.client, settings.embedding_model)
    return model
