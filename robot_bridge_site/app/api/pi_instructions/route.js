import { NextResponse } from "next/server";
import {
  getPiInstructions,
  addPiInstruction,
  acknowledgeInstruction,
  clearAllInstructions,
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
 * GET /api/pi_instructions
 * Parameters:
 * - ?pending=true : returns only pending (unexecuted) instructions
 * - ?latest=true  : returns only the single newest instruction
 * - ?ack=<ID>     : marks specified instruction as executed
 * - ?clear=true   : resets/clears instruction queue
 */
export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const pendingOnly = searchParams.get("pending") === "true";
  const latestOnly = searchParams.get("latest") === "true";
  const ackId = searchParams.get("ack");
  const clear = searchParams.get("clear");
  const limit = parseInt(searchParams.get("limit") || "50", 10);

  // Clear handler via GET
  if (clear === "true") {
    await clearAllInstructions();
    return NextResponse.json(
      { success: true, message: "All instructions cleared." },
      { status: 200, headers: CORS_HEADERS }
    );
  }

  // Acknowledge handler via GET
  if (ackId) {
    const status = searchParams.get("status") || "executed";
    const updated = await acknowledgeInstruction(ackId, status);
    return NextResponse.json(
      { success: true, message: `Instruction ${ackId} marked as ${status}`, item: updated },
      { status: 200, headers: CORS_HEADERS }
    );
  }

  // Normal retrieval
  const instructions = await getPiInstructions(limit, pendingOnly);

  if (latestOnly) {
    return NextResponse.json(
      {
        latest: instructions[0] || null,
        count: instructions.length,
      },
      { status: 200, headers: CORS_HEADERS }
    );
  }

  return NextResponse.json(
    {
      count: instructions.length,
      pending_count: instructions.filter((i) => i.status === "pending").length,
      instructions,
    },
    { status: 200, headers: CORS_HEADERS }
  );
}

/**
 * PATCH /api/pi_instructions
 * Body: { "id": "inst_123", "status": "executed" }
 * Used by Raspberry Pi to acknowledge execution.
 */
export async function PATCH(request) {
  try {
    const body = await request.json();
    const { id, status = "executed" } = body;
    if (!id) {
      return NextResponse.json(
        { success: false, error: "Missing 'id' in JSON body" },
        { status: 400, headers: CORS_HEADERS }
      );
    }
    const updated = await acknowledgeInstruction(id, status);
    return NextResponse.json(
      { success: true, message: `Instruction ${id} marked as ${status}`, item: updated },
      { status: 200, headers: CORS_HEADERS }
    );
  } catch (err) {
    return NextResponse.json(
      { success: false, error: err.message || "Failed to update status" },
      { status: 500, headers: CORS_HEADERS }
    );
  }
}

/**
 * DELETE /api/pi_instructions
 * Clears instructions list.
 */
export async function DELETE() {
  await clearAllInstructions();
  return NextResponse.json(
    { success: true, message: "Instruction queue cleared successfully." },
    { status: 200, headers: CORS_HEADERS }
  );
}
