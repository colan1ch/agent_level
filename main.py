import logging
import time
import uuid
import json
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Coursework LLM Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OLLAMA_API_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"
TRANSPORT_API_URL = "http://10.241.116.6:8080/segment"
REQUEST_TIMEOUT = 300.0
NUM_SEGMENTS = 3  # всегда режем на 3 сегмента


class AskRequest(BaseModel):
    documentation: str
    question: str


class SegmentPayload(BaseModel):
    message_id: str
    total_segments: int
    segment_number: int
    payload: str


async def get_full_response_from_ollama(
    documentation: str, question: str
) -> str:
    """
    Get complete response from Ollama and accumulate it fully.
    """
    prompt = f"""Ответь на вопрос на основе документации. Документация: {documentation}\nВопрос: {question}\nОтвет:"""

    ollama_payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,  # Get full response at once
    }

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(
                OLLAMA_API_URL,
                json=ollama_payload
            )

            if response.status_code != 200:
                logger.error(f"Ollama error: {response.status_code}")
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": True,
                        "message": f"Ollama returned {response.status_code}"
                    }
                )

            response_data = response.json()
            full_response = response_data.get("response", "").strip()

            if not full_response:
                logger.error("Ollama returned empty response")
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": True,
                        "message": "Ollama returned empty response"
                    }
                )

            logger.info(f"Got full response from Ollama: {len(full_response)} characters")
            return full_response

    except httpx.ConnectError as e:
        logger.error(f"Connection to Ollama failed: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": True,
                "message": "Cannot connect to Ollama. Make sure it's running on localhost:11434"
            }
        )
    except httpx.TimeoutException as e:
        logger.error(f"Ollama request timeout: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": True,
                "message": "Ollama request timeout"
            }
        )
    except Exception as e:
        logger.error(f"Unexpected error getting response from Ollama: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": True,
                "message": str(e)
            }
        )


def split_into_segments(text: str, num_segments: int) -> list:
    """
    Split text into a fixed number of segments (by characters).
    """
    if num_segments <= 0:
        return [text]
    
    total_len = len(text)
    segment_len = total_len // num_segments
    remainder = total_len % num_segments
    
    segments = []
    start = 0
    for i in range(num_segments):
        # add one extra character for first 'remainder' segments
        end = start + segment_len + (1 if i < remainder else 0)
        segments.append(text[start:end])
        start = end
    
    logger.info(f"Split response into {len(segments)} segments (requested {num_segments})")
    return segments


async def send_segments_to_transport(
    message_id: str,
    segments: list
) -> int:
    """
    Send all segments to transport layer and return count of successfully sent segments.
    """
    total_segments = len(segments)
    segments_sent = 0

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for segment_number, payload in enumerate(segments, start=1):
                segment = SegmentPayload(
                    message_id=message_id,
                    total_segments=total_segments,
                    segment_number=segment_number,
                    payload=payload
                )

                try:
                    transport_response = await client.post(
                        TRANSPORT_API_URL,
                        json=segment.dict(),
                        timeout=10.0
                    )

                    if transport_response.status_code == 200:
                        segments_sent += 1
                        logger.info(
                            f"Segment {segment_number}/{total_segments} sent "
                            f"(msg: {message_id}, size: {len(payload)} chars)"
                        )
                    else:
                        logger.error(
                            f"Transport layer returned {transport_response.status_code} "
                            f"for segment {segment_number}"
                        )

                except httpx.ConnectError as e:
                    logger.error(
                        f"Cannot connect to transport layer at {TRANSPORT_API_URL}: {e}"
                    )
                except httpx.TimeoutException as e:
                    logger.error(f"Transport layer timeout for segment {segment_number}: {e}")
                except Exception as e:
                    logger.error(f"Error sending segment {segment_number}: {e}")

        logger.info(
            f"Message {message_id}: Successfully sent {segments_sent}/{total_segments} segments"
        )
        return segments_sent

    except Exception as e:
        logger.error(f"Unexpected error sending segments: {e}")
        raise


async def process_request(
    documentation: str, question: str
) -> dict:
    """
    Main request processing:
    1. Get full response from Ollama
    2. Split into fixed number of segments (NUM_SEGMENTS)
    3. Send each segment to transport layer
    4. Return status
    """
    # Generate unique message ID
    message_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
    logger.info(f"Processing request with message_id: {message_id}")

    # Get full response from Ollama
    full_response = await get_full_response_from_ollama(documentation, question)

    # Split into fixed number of segments
    segments = split_into_segments(full_response, NUM_SEGMENTS)

    if not segments:
        logger.error("No segments created")
        raise HTTPException(
            status_code=500,
            detail={
                "error": True,
                "message": "Failed to create segments from response"
            }
        )

    # Send segments to transport layer
    segments_sent = await send_segments_to_transport(message_id, segments)

    return {
        "status": "ok",
        "segments_sent": segments_sent,
        "message_id": message_id
    }


@app.post("/ask")
async def ask(request: AskRequest):
    """
    Main endpoint:
    - Accepts documentation and question
    - Gets full response from Ollama
    - Splits into fixed number of segments (NUM_SEGMENTS = 3)
    - Sends each segment to transport layer
    - Returns status
    """
    logger.info(
        f"Incoming request - Question: {request.question[:100]}... | Docs length: {len(request.documentation)}"
    )

    result = await process_request(request.documentation, request.question)
    return result


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

