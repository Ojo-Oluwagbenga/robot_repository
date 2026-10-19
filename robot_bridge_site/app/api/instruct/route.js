import { NextResponse } from "next/server";
import { addPiInstruction, getBioData } from "@/lib/db";

// Standard CORS headers for unrestricted access from Gemini / Raspberry Pi / Apps
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
 * GET /api/instruct?code=<CODE>&value=<VALUE>
 * Primary endpoint for Gemini to trigger robot actions during conversation.
 */
export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const code = searchParams.get("code");
  const value = searchParams.get("value") ?? "";
  const source = searchParams.get("source") || "gemini";

  if (!code) {
    const bio = await getBioData();
    const validCodes = Array.isArray(bio.codes)
      ? bio.codes.map((c) => c.code)
      : Object.keys(bio.codes || {});

    return NextResponse.json(
      {
        success: false,
        error: "Missing required 'code' parameter in GET request.",
        usage: "GET /api/instruct?code=<CODE>&value=<VALUE>",
        valid_codes: validCodes,
        example: "/api/instruct?code=NOD_UP&value=30",
      },
      { status: 400, headers: CORS_HEADERS }
    );
  }

  try {
    const instruction = await addPiInstruction(code, value, source);
    return NextResponse.json(
      {
        success: true,
        message: `Instruction '${code}' recorded into pi_instructions.`,
        instruction,
      },
      { status: 200, headers: CORS_HEADERS }
    );
  } catch (error) {
    return NextResponse.json(
      {
        success: false,
        error: error.message || "Failed to record instruction.",
      },
      { status: 500, headers: CORS_HEADERS }
    );
  }
}

/**
 * POST /api/instruct
 * Body: { "code": "NOD_UP", "value": "30", "source": "gemini" }
 */
export async function POST(request) {
  try {
    const body = await request.json();
    const code = body.code;
    const value = body.value ?? "";
    const source = body.source || "gemini";

    if (!code) {
      return NextResponse.json(
        {
          success: false,
          error: "Missing 'code' in JSON request body.",
          usage: '{ "code": "NOD_UP", "value": "30" }',
        },
        { status: 400, headers: CORS_HEADERS }
      );
    }

    const instruction = await addPiInstruction(code, value, source);
    return NextResponse.json(
      {
        success: true,
        message: `Instruction '${code}' recorded into pi_instructions.`,
        instruction,
      },
      { status: 200, headers: CORS_HEADERS }
    );
  } catch (error) {
    return NextResponse.json(
      {
        success: false,
        error: error.message || "Invalid JSON or server error.",
      },
      { status: 500, headers: CORS_HEADERS }
    );
  }
}
