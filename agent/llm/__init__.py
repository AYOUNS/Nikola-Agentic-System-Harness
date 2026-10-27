"""agent.llm — LLM connectivity layer.

Three-layer dispatcher pattern:
  client.py     → make_client()  — creates the raw openai.OpenAI instance
  types.py      → ChatCompletionCall Protocol and type aliases
  dispatcher.py → make_chat_completions_effect(), dispatch()
"""
