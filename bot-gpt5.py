#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import asyncio
import os
import sys

from dotenv import load_dotenv
from loguru import logger

from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat_window_functions import (
    handle_list_windows,
    handle_remember_window,
    handle_send_text_to_window,
    list_windows_schema,
    remember_window_schema,
    send_text_to_window_schema,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.services.openai.base_llm import BaseOpenAILLMService
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.stt import OpenAISTTService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.processors.frameworks.rtvi import RTVIConfig, RTVIObserver, RTVIProcessor
from pipecat.audio.vad.silero import SileroVADAnalyzer

load_dotenv(override=True)

# Load system instruction from file
with open("prompt-realtime-api.txt", "r") as f:
    SYSTEM_INSTRUCTION = f.read()

# SYSTEM_INSTRUCTION = "Be a helpful assistant."

# Create tools schema with window control functions
tools = ToolsSchema(
    standard_tools=[
        list_windows_schema,
        remember_window_schema,
        send_text_to_window_schema,
    ]
)


# We store functions so objects (e.g. SileroVADAnalyzer) don't get
# instantiated. The function will be called when the desired transport gets
# selected.
transport_params = {
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        vad_analyzer=SileroVADAnalyzer(),
    ),
}


# Arguably this should be the default
class TTSNoEmptyStrings(OpenAITTSService):
    async def run_tts(self, text: str):
        logger.info(f"[TTS] Running TTS for text: {text}")
        # strip whitespace and punctuation
        text = text.strip().strip(".,;:!?'-\"‘’“”()[]{}")
        # if text is empty, skip
        if not text:
            logger.info("[TTS] Skipping empty text")
            return

        async for chunk in super().run_tts(text):
            yield chunk


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    logger.info("Starting bot")

    stt = OpenAISTTService(
        api_key=os.getenv("OPENAI_API_KEY"),
    )
    llm = OpenAILLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-5",
        params=BaseOpenAILLMService.InputParams(
            extra={
                "service_tier": "priority",
                "reasoning_effort": "minimal",
            },
        ),
    )
    tts = TTSNoEmptyStrings(
        api_key=os.getenv("OPENAI_API_KEY"),
    )

    # Register window control functions
    llm.register_function("list_windows", handle_list_windows)
    llm.register_function("remember_window", handle_remember_window)
    llm.register_function("send_text_to_window", handle_send_text_to_window)

    # Create a standard OpenAI LLM context object using the normal messages format. The
    # OpenAIRealtimeBetaLLMService will convert this internally to messages that the
    # openai WebSocket API can understand.
    context = OpenAILLMContext(
        [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": "Say hello!"},
        ],
        tools,
    )
    context_aggregator = llm.create_context_aggregator(context)

    rtvi = RTVIProcessor(config=RTVIConfig(config=[]))

    pipeline = Pipeline(
        [
            transport.input(),  # Transport user input
            rtvi,
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=[RTVIObserver(rtvi)],
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected")
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)

    await runner.run(task)


async def bot(runner_args: RunnerArguments):
    """Main bot entry point compatible with Pipecat Cloud."""
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    # look for '-t' 'local' in sys.argv and run the bot directly
    if any(a == "-t" and b == "local" for a, b in zip(sys.argv, sys.argv[1:])):
        print("Using local transport")

        from macos.local_mac_transport import (
            LocalMacTransport,
            LocalMacTransportParams,
        )
        from pipecat.audio.vad.silero import SileroVADAnalyzer

        params = LocalMacTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
        )
        transport = LocalMacTransport(params=params)
        asyncio.run(run_bot(transport, RunnerArguments()))
        sys.exit(0)

    else:
        from pipecat.runner.run import main

        main()
