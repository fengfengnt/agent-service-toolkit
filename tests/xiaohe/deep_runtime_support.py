from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field


class DeepScriptModel(FakeMessagesListChatModel):
    bound_tool_names: list[str] = Field(default_factory=list)

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        self.bound_tool_names = [convert_to_openai_tool(tool)["function"]["name"] for tool in tools]
        return self


def make_script_model(messages: list[AIMessage]) -> BaseChatModel:
    return DeepScriptModel(responses=messages, profile={"tool_calling": True})
