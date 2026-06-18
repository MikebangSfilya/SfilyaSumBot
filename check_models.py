import os
import asyncio
from openai import AsyncOpenAI

# Вставь свой ключ или убедись, что он есть в переменных окружения
API_KEY = os.getenv("LLM_API_KEY", "").strip()


async def main():
    client = AsyncOpenAI(
        api_key=API_KEY,
        base_url="https://api.sambanova.ai/v1"
    )

    print("Доступные модели на SambaNova:")
    models = await client.models.list()

    for idx, model in enumerate(models.data, 1):
        print(f"{idx}. {model.id}")


if __name__ == "__main__":
    asyncio.run(main())