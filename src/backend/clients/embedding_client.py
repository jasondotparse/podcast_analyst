"""OpenAI embeddings. Passages and search queries must use the same model and dimensions."""

from openai import AsyncOpenAI


class EmbeddingClient:
    MODEL, DIMENSIONS = "text-embedding-3-large", 1536  # Matches passages.embedding vector(1536).

    def __init__(self, openai: AsyncOpenAI):
        self.openai = openai

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self.openai.embeddings.create(model=self.MODEL, dimensions=self.DIMENSIONS, input=texts)
        return [item.embedding for item in response.data]
