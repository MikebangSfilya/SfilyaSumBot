import logging

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

logger = logging.getLogger("SumBot.tracing")

_provider: TracerProvider | None = None


def configure_tracing(
    *,
    enabled: bool,
    service_name: str,
    endpoint: str,
    sample_ratio: float,
) -> None:
    global _provider

    if not enabled:
        logger.info("Tracing is disabled.")
        return
    if _provider is not None:
        return

    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name}),
        sampler=ParentBased(TraceIdRatioBased(sample_ratio)),
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _provider = provider
    logger.info(
        "Tracing configured (service_name=%s, endpoint=%s, sample_ratio=%.3f)",
        service_name,
        endpoint,
        sample_ratio,
    )


def shutdown_tracing() -> None:
    if _provider is None:
        return
    _provider.shutdown()
