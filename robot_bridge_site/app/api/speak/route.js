import { NextResponse } from "next/server";
import { addPhoneSpeech } from "@/lib/db";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
  "Cache-Control": "no-store, no-cache, must-revalidate",
};

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: CORS_HEADERS });
}

/**
 * Fast endpoint to dispatch speech to phone:
 * GET /api/speak?text=Hello+Yohanna
 */
export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const text = searchParams.get("text") || searchParams.get("val") || searchParams.get("msg") || "";
  const source = searchParams.get("source") || "pi";

  if (!text || !text.trim()) {
    return NextResponse.json(
      { success: false, error: "Missing 'text' query parameter. Example: /api/speak?text=Hello" },
      { status: 400, headers: CORS_HEADERS }
    );
  }

  try {
    const packet = await addPhoneSpeech(text.trim(), source);
    return NextResponse.json(
      { success: true, message: "Dispatched to phone speaker", packet },
      { status: 201, headers: CORS_HEADERS }
    );
  } catch (err) {
    return NextResponse.json(
      { success: false, error: err.message },
      { status: 500, headers: CORS_HEADERS }
    );
  }
}

/**
 * Fast endpoint to dispatch speech to phone:
 * POST /api/speak
 * Body: { "text": "Hello Yohanna" }
 */
export async function POST(request) {
  try {
    let text = "";
    let source = "pi";

    const contentType = request.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      const body = await request.json();
      text = body.text || body.val || body.message || "";
      source = body.source || "pi";
    } else {
      text = await request.text();
    }

    if (!text || !text.trim()) {
      return NextResponse.json(
        { success: false, error: "Missing 'text' in payload" },
        { status: 400, headers: CORS_HEADERS }
      );
    }

    const packet = await addPhoneSpeech(text.trim(), source);
    return NextResponse.json(
      { success: true, message: "Dispatched to phone speaker", packet },
      { status: 201, headers: CORS_HEADERS }
    );
  } catch (err) {
    return NextResponse.json(
      { success: false, error: err.message },
      { status: 500, headers: CORS_HEADERS }
    );
  }
}
