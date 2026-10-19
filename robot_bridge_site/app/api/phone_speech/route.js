import { NextResponse } from "next/server";
import {
  getPhoneSpeech,
  addPhoneSpeech,
  acknowledgePhoneSpeech,
  clearAllPhoneSpeech,
} from "@/lib/db";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
  "Cache-Control": "no-store, no-cache, must-revalidate",
};

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: CORS_HEADERS });
}

/**
 * GET /api/phone_speech
 * Parameters:
 * - ?pending=true : returns only pending (unspoken) speech packets for phone
 * - ?latest=true  : returns only the single newest speech packet
 * - ?ack=<ID>     : marks specified speech packet as spoken
 * - ?clear=true   : resets/clears speech queue
 * - ?text=<TEXT>  : convenience shortcut to queue speech via GET query
 */
export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const pendingOnly = searchParams.get("pending") === "true";
  const latestOnly = searchParams.get("latest") === "true";
  const ackId = searchParams.get("ack");
  const clear = searchParams.get("clear");
  const textParam = searchParams.get("text");
  const source = searchParams.get("source") || "pi";
  const limit = parseInt(searchParams.get("limit") || "50", 10);

  // Clear handler via GET
  if (clear === "true") {
    await clearAllPhoneSpeech();
    return NextResponse.json(
      { success: true, message: "All phone speech cleared." },
      { status: 200, headers: CORS_HEADERS }
    );
  }

  // Acknowledge handler via GET
  if (ackId) {
    const status = searchParams.get("status") || "spoken";
    const updated = await acknowledgePhoneSpeech(ackId, status);
    return NextResponse.json(
      { success: true, message: `Speech packet ${ackId} marked as ${status}`, item: updated },
      { status: 200, headers: CORS_HEADERS }
    );
  }

  // Quick dispatch via GET ?text=...
  if (textParam && textParam.trim()) {
    try {
      const packet = await addPhoneSpeech(textParam.trim(), source);
      return NextResponse.json(
        { success: true, message: "Dispatched text to phone", packet },
        { status: 201, headers: CORS_HEADERS }
      );
    } catch (err) {
      return NextResponse.json(
        { success: false, error: err.message },
        { status: 400, headers: CORS_HEADERS }
      );
    }
  }

  // Normal retrieval
  const speechList = await getPhoneSpeech(limit, pendingOnly);

  if (latestOnly) {
    return NextResponse.json(
      {
        latest: speechList[0] || null,
        count: speechList.length,
      },
      { status: 200, headers: CORS_HEADERS }
    );
  }

  return NextResponse.json(
    {
      count: speechList.length,
      pending_count: speechList.filter((i) => i.status === "pending").length,
      speech: speechList,
    },
    { status: 200, headers: CORS_HEADERS }
  );
}

/**
 * POST /api/phone_speech
 * Accepts JSON: { "text": "...", "source": "pi" }
 * Enqueues text for the phone app to speak out loud.
 */
export async function POST(request) {
  try {
    let text = "";
    let source = "pi";

    const contentType = request.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      const body = await request.json();
      text = body.text || body.message || body.value || "";
      source = body.source || "pi";
    } else {
      const formText = await request.text();
      text = formText;
    }

    if (!text || !text.trim()) {
      return NextResponse.json(
        { success: false, error: "Missing 'text' in payload" },
        { status: 400, headers: CORS_HEADERS }
      );
    }

    const packet = await addPhoneSpeech(text.trim(), source);
    return NextResponse.json(
      {
        success: true,
        message: "Dispatched text to phone speaker",
        packet,
      },
      { status: 201, headers: CORS_HEADERS }
    );
  } catch (err) {
    return NextResponse.json(
      { success: false, error: err.message || "Failed to dispatch speech" },
      { status: 500, headers: CORS_HEADERS }
    );
  }
}

/**
 * PATCH /api/phone_speech
 * Body: { "id": "speech_123", "status": "spoken" }
 * Used by Phone to acknowledge audio vocalization.
 */
export async function PATCH(request) {
  try {
    const body = await request.json();
    const { id, status = "spoken" } = body;
    if (!id) {
      return NextResponse.json(
        { success: false, error: "Missing 'id' in JSON body" },
        { status: 400, headers: CORS_HEADERS }
      );
    }
    const updated = await acknowledgePhoneSpeech(id, status);
    return NextResponse.json(
      { success: true, message: `Speech packet ${id} marked as ${status}`, item: updated },
      { status: 200, headers: CORS_HEADERS }
    );
  } catch (err) {
    return NextResponse.json(
      { success: false, error: err.message || "Failed to update speech status" },
      { status: 500, headers: CORS_HEADERS }
    );
  }
}

/**
 * DELETE /api/phone_speech
 * Clears speech list.
 */
export async function DELETE() {
  await clearAllPhoneSpeech();
  return NextResponse.json(
    { success: true, message: "Phone speech queue cleared successfully." },
    { status: 200, headers: CORS_HEADERS }
  );
}
