"""ReAct Agent strategies for Code."""

import re

from typing import Any, Dict, Tuple

import tiktoken

from langchain_core.language_models.chat_models import BaseChatModel
from tiktoken.core import Encoding

from agential.cog.functional.react import _is_halted, _prompt_agent
from agential.cog.strategies.react.base import ReActBaseStrategy
from agential.utils.general import safe_execute


def parse_math_action(action: str) -> Tuple[str, str]:
    """Parses an action string to extract the action type and code content.

    Identifies action types (`Finish`, `Calculate`) and extracts the
    corresponding code content enclosed within Markdown-style code blocks.
    The action type is case-insensitive and the code content is trimmed of
    leading and trailing whitespace.

    Args:
        action (str): The action string containing the action type and code content.

    Returns:
        Tuple[str, str]: A tuple containing the extracted action type (capitalized)
        and the extracted code content.
    """
    action_split = action.split("```python", maxsplit=1)
    match = re.search(r"\b(Finish|Calculate)\b", action_split[0], re.IGNORECASE)

    action_type = match.group(0).lower().capitalize() if match else ""
    try:
        query = action_split[1].split("```")[0].strip() if action_type else ""
    except:
        action_type = ""
        query = ""

    return action_type, query


class ReActMathStrategy(ReActBaseStrategy):
    """A strategy class for Math benchmarks using the ReAct agent.

    Attributes:
        llm (BaseChatModel): The language model used for generating answers and critiques.
        max_steps (int): The maximum number of steps the agent can take.
        max_tokens (int): The maximum number of tokens allowed for a response.
        enc (Encoding): The encoding used for the language model.
    """

    def __init__(
        self,
        llm: BaseChatModel,
        max_steps: int = 6,
        max_tokens: int = 3896,
        enc: Encoding = tiktoken.encoding_for_model("gpt-3.5-turbo"),
    ) -> None:
        """Initialization."""
        super().__init__(llm)
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self.enc = enc

        self._scratchpad = ""
        self._current_answer = ""
        self._finished = False

    def generate(
        self,
        question: str,
        examples: str,
        prompt: str,
        additional_keys: Dict[str, str],
        **kwargs: Any,
    ) -> str:
        """Generates a thought based on the question, examples, and prompt.

        Args:
            question (str): The question to be answered.
            examples (str): Examples to guide the generation process.
            prompt (str): The prompt used for generating the thought.
            additional_keys (Dict[str, str]): Additional keys for the generation process.
            **kwargs (Any): Additional arguments.

        Returns:
            str: The generated thought.
        """
        max_steps = kwargs.get("max_steps", self.max_steps)  # type: ignore

        self._scratchpad += "\nThought:"
        thought = (
            _prompt_agent(
                llm=self.llm,
                question=question,
                scratchpad=self._scratchpad,
                examples=examples,
                max_steps=max_steps,  # type: ignore
                prompt=prompt,
                additional_keys=additional_keys,
            )
            .split("Action")[0]
            .strip()
            .split("\n")[0]
        )
        self._scratchpad += " " + thought

        return thought

    def generate_action(
        self,
        question: str,
        examples: str,
        prompt: str,
        additional_keys: Dict[str, str],
        **kwargs: Any,
    ) -> Tuple[str, str]:
        """Generates an action based on the question, examples, and prompt.

        Args:
            question (str): The question to be answered.
            examples (str): Examples to guide the generation process.
            prompt (str): The prompt used for generating the action.
            additional_keys (Dict[str, str]): Additional keys for the generation process.
            **kwargs (Any): Additional arguments.

        Returns:
            Tuple[str, str]: The generated action type and code.
        """
        max_steps = kwargs.get("max_steps", self.max_steps)
        self._scratchpad += "\nAction:"
        action = _prompt_agent(
            llm=self.llm,
            question=question,
            scratchpad=self._scratchpad,
            examples=examples,
            max_steps=max_steps,  # type: ignore
            prompt=prompt,
            additional_keys=additional_keys,
        )
        action = action.split("Observation")[0].strip()

        action_type, query = parse_math_action(action)
        self._scratchpad += f" {action_type}[\n```python\n{query}\n```\n]"

        return action_type, query

    def generate_observation(self, idx: int, action_type: str, query: str) -> str:
        """Generates an observation based on the action type and query.

        Args:
            idx (int): The index of the observation.
            action_type (str): The type of action to be performed.
            query (str): The query for the action.

        Returns:
            str: The generated observation.
        """
        self._scratchpad += f"\nObservation {idx}: "
        if action_type.lower() == "finish":
            self._current_answer = query
            self._finished = True
            obs = f"\n```python\n{self._current_answer}\n```"
        elif action_type.lower() == "calculate":
            answer, execution_status = safe_execute(query)
            self._current_answer = query
            obs = f"\n```python\n{self._current_answer}\n```\nExecution Status: {execution_status}\nOutput: answer = {answer[0]}"
        else:
            obs = (
                "Invalid Action. Valid Actions are Calculate[code] and Finish[answer]."
            )
        self._scratchpad += obs

        return obs

    def create_output_dict(
        self, thought: str, action_type: str, query: str, obs: str
    ) -> Dict[str, str]:
        """Creates a dictionary of the output components.

        Args:
            thought (str): The generated thought.
            action_type (str): The type of action performed.
            query (str): The query for the action.
            obs (str): The generated observation.

        Returns:
            Dict[str, str]: A dictionary containing the thought, action type, query, observation, and answer.
        """
        return {
            "thought": thought,
            "action_type": action_type,
            "query": query,
            "observation": obs,
            "answer": self._current_answer,
        }

    def halting_condition(
        self,
        idx: int,
        question: str,
        examples: str,
        prompt: str,
        additional_keys: Dict[str, str],
        **kwargs: Any,
    ) -> bool:
        """Determines whether the halting condition has been met.

        Args:
            idx (int): The current step index.
            question (str): The question being answered.
            examples (str): Examples to guide the generation process.
            prompt (str): The prompt used for generating the thought and action.
            additional_keys (Dict[str, str]): Additional keys for the generation process.
            **kwargs (Any): Additional arguments.

        Returns:
            bool: True if the halting condition is met, False otherwise.
        """
        max_steps = kwargs.get("max_steps", self.max_steps)

        return _is_halted(
            finished=self._finished,
            idx=idx,
            question=question,
            scratchpad=self._scratchpad,
            examples=examples,
            max_steps=max_steps,  # type: ignore
            max_tokens=self.max_tokens,
            enc=self.enc,
            prompt=prompt,
            additional_keys=additional_keys,
        )

    def reset(self, **kwargs: Any) -> None:
        """Resets the internal state of the strategy.

        Resets the current answer, scratchpad, and the finished flag.

        Args:
            **kwargs (Any): Additional arguments.

        Returns:
            None
        """
        self._current_answer = ""
        self._scratchpad = ""
        self._finished = False


class ReActGSM8KStrategy(ReActMathStrategy):
    """A strategy class for the GSM8K benchmark using the ReAct agent."""

    pass


class ReActSVAMPStrategy(ReActMathStrategy):
    """A strategy class for the SVAMP benchmark using the ReAct agent."""

    pass


class ReActTabMWPStrategy(ReActMathStrategy):
    """A strategy class for the TabMWP benchmark using the ReAct agent."""

    pass
