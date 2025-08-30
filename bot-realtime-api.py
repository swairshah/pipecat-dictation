#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#


import os

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
from pipecat.frames.frames import TranscriptionMessage
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.processors.transcript_processor import TranscriptProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.openai_realtime_beta import (
    InputAudioNoiseReduction,
    InputAudioTranscription,
    OpenAIRealtimeBetaLLMService,
    SemanticTurnDetection,
    SessionProperties,
)
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.processors.frameworks.rtvi import RTVIConfig, RTVIObserver, RTVIProcessor


load_dotenv(override=True)

# Load system instruction from file
# with open("prompt-realtime-api.txt", "r") as f:
#     SYSTEM_INSTRUCTION = f.read()

SYSTEM_INSTRUCTION = "Be a helpful assistant."

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
    ),
}


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    logger.info("Starting bot")

    session_properties = SessionProperties(
        input_audio_transcription=InputAudioTranscription(),
        # Set openai TurnDetection parameters. Not setting this at all will turn it
        # on by default
        turn_detection=SemanticTurnDetection(),
        # Or set to False to disable openai turn detection and use transport VAD
        # turn_detection=False,
        input_audio_noise_reduction=InputAudioNoiseReduction(type="near_field"),
        instructions=SYSTEM_INSTRUCTION,
    )

    llm = OpenAIRealtimeBetaLLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
        session_properties=session_properties,
        start_audio_paused=False,
        model="gpt-realtime",
    )

    # Register window control functions
    llm.register_function("list_windows", handle_list_windows)
    llm.register_function("remember_window", handle_remember_window)
    llm.register_function("send_text_to_window", handle_send_text_to_window)

    transcript = TranscriptProcessor()

    # Create a standard OpenAI LLM context object using the normal messages format. The
    # OpenAIRealtimeBetaLLMService will convert this internally to messages that the
    # openai WebSocket API can understand.
    context = OpenAILLMContext(
        [{"role": "user", "content": "Say hello!"}],
        tools,
    )

    context_aggregator = llm.create_context_aggregator(context)

    rtvi = RTVIProcessor(config=RTVIConfig(config=[]))

    pipeline = Pipeline(
        [
            transport.input(),  # Transport user input
            context_aggregator.user(),
            rtvi,
            llm,  # LLM
            transcript.user(),  # Placed after the LLM, as LLM pushes TranscriptionFrames downstream
            transport.output(),  # Transport bot output
            transcript.assistant(),  # After the transcript output, to time with the audio output
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

    # Local-style transports (no external client connect) should start immediately.
    is_local_style = os.getenv("TRANSPORT", "webrtc").lower() != "webrtc"
    if is_local_style:
        logger.info("Starting conversation ...")
        await task.queue_frames([context_aggregator.user().get_context_frame()])
    else:

        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport, client):
            logger.info("Client connected")
            await task.queue_frames([context_aggregator.user().get_context_frame()])

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info("Client disconnected")
            await task.cancel()

    # Register event handler for transcript updates
    @transcript.event_handler("on_transcript_update")
    async def on_transcript_update(processor, frame):
        for msg in frame.messages:
            if isinstance(msg, TranscriptionMessage):
                timestamp = f"[{msg.timestamp}] " if msg.timestamp else ""
                line = f"{timestamp}{msg.role}: {msg.content}"
                logger.info(f"Transcript: {line}")

    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)

    await runner.run(task)


async def bot(runner_args: RunnerArguments):
    """Main bot entry point compatible with Pipecat Cloud."""
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    import asyncio
    import os as _os

    transport_env = _os.getenv("TRANSPORT", "webrtc").lower()
    if transport_env == "local":
        from pipecat.runner.types import RunnerArguments

        # Use the built-in Pipecat local transport (PyAudio)
        from pipecat.transports.local.audio import (
            LocalAudioTransport,
            LocalAudioTransportParams,
        )

        in_dev = _os.getenv("LOCAL_AUDIO_INPUT_DEVICE")
        out_dev = _os.getenv("LOCAL_AUDIO_OUTPUT_DEVICE")
        params = LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=24000,
            audio_in_channels=1,
            audio_out_channels=1,
            audio_out_10ms_chunks=4,  # 40ms
            input_device_index=int(in_dev) if in_dev else None,
            output_device_index=int(out_dev) if out_dev else None,
        )
        transport = LocalAudioTransport(params=params)
        asyncio.run(run_bot(transport, RunnerArguments()))
    elif transport_env == "local-aec":
        from pipecat.runner.types import RunnerArguments
        from local_aec_transport import LocalAECTransport, LocalAECTransportParams

        in_dev = _os.getenv("LOCAL_AUDIO_INPUT_DEVICE")
        out_dev = _os.getenv("LOCAL_AUDIO_OUTPUT_DEVICE")
        params = LocalAECTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,  # use 16 kHz to avoid resampling
            audio_in_channels=1,
            audio_out_channels=1,
            audio_out_10ms_chunks=4,  # 40ms
            input_device_index=int(in_dev) if in_dev else None,
            output_device_index=int(out_dev) if out_dev else None,
            aec_enabled=True,
            ns_enabled=True,
            agc_enabled=False,
            hpf_enabled=True,
            aec_sample_rate=16000,
        )
        transport = LocalAECTransport(params=params)
        asyncio.run(run_bot(transport, RunnerArguments()))
    elif transport_env == "local-aec-mac":
        logger.info("Using new AEC transport")

        from pipecat.runner.types import RunnerArguments
        from local_vpio_transport import (
            LocalVPIOTransport,
            LocalVPIOTransportParams,
        )

        params = LocalVPIOTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
            audio_in_channels=1,
            audio_out_channels=1,
            audio_out_10ms_chunks=1,  # 10ms frames
            ring_capacity_secs=8.0,   # smaller base; staging grows dynamically
            preroll_ms=40,
            slice_ms=5,
            playback_headroom_ms=10,
        )
        transport = LocalVPIOTransport(params=params)
        asyncio.run(run_bot(transport, RunnerArguments()))
    else:
        from pipecat.runner.run import main

        main()
