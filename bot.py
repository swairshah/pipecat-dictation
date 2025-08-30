import asyncio
import os
import traceback

from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.runner.types import RunnerArguments
from pipecat.services.google.llm import GoogleLLMService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport
from pipecat.services.speechmatics.stt import SpeechmaticsSTTService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.processors.frameworks.rtvi import RTVIConfig, RTVIObserver, RTVIProcessor
from macos.local_mac_transport import (
    LocalMacTransport,
    LocalMacTransportParams,
)

load_dotenv(override=True)


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    try:
        stack = "".join(traceback.format_stack())
        logger.warning("run_bot() invoked. Full call stack follows:\n" + stack)
    except Exception:
        pass
    logger.info("Starting bot with Gemini TTS")

    stt_speechmatics = SpeechmaticsSTTService(
        api_key=os.getenv("SPEECHMATICS_API_KEY"),
        params=SpeechmaticsSTTService.InputParams(
            language=Language.EN,
        ),
    )

    stt_deepgram = DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))
    stt = stt_deepgram

    llm = GoogleLLMService(
        api_key=os.getenv("GOOGLE_API_KEY"),
        model="gemini-2.5-flash",
    )

    tts = CartesiaTTSService(
        api_key=os.getenv("CARTESIA_API_KEY"),
        voice_id="71a7ad14-091c-4e8e-a314-022ece01c121",  # British Reading Lady
    )

    rtvi = RTVIProcessor(config=RTVIConfig(config=[]))

    # System message that instructs the AI on how to speak
    messages = [
        {
            "role": "system",
            "content": """You are a helpful assistant. Respond to what the user said in a creative and helpful way.""",
        },
    ]

    context = OpenAILLMContext(messages)
    context_aggregator = llm.create_context_aggregator(context)

    pipeline = Pipeline(
        [
            transport.input(),  # Transport user input
            rtvi,
            stt,  # STT
            context_aggregator.user(),  # User responses
            llm,  # LLM
            tts,  # Gemini TTS
            transport.output(),  # Transport bot output
            context_aggregator.assistant(),  # Assistant spoken responses
        ]
    )
    logger.warning(f"run_bot(): constructed Pipeline object id={id(pipeline)}")

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=[RTVIObserver(rtvi)],
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
    )
    logger.warning(f"run_bot(): constructed PipelineTask name={task.name} id={id(task)}")

    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)
    logger.warning(f"run_bot(): constructed PipelineRunner name={runner.name} id={id(runner)}")

    await runner.run(task)


if __name__ == "__main__":
    logger.info("Using new AEC transport")

    params = LocalMacTransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        vad_analyzer=SileroVADAnalyzer(),
    )
    transport = LocalMacTransport(params=params)
    asyncio.run(run_bot(transport, RunnerArguments()))
