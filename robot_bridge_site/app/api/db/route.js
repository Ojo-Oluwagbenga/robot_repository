import { NextResponse } from "next/server";
import { getFullDb, clearAllInstructions } from "@/lib/db";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
  "Cache-Control": "no-store, no-cache, must-revalidate",
};

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: CORS_HEADERS });
}

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const format = searchParams.get("format");
  const dbData = await getFullDb();

  if (format === "text") {
    return new NextResponse(JSON.stringify(dbData, null, 2), {
      status: 200,
      headers: {
        ...CORS_HEADERS,
        "Content-Type": "text/plain; charset=utf-8",
      },
    });
  }

  return NextResponse.json(dbData, { status: 200, headers: CORS_HEADERS });
}

export async function DELETE() {
  await clearAllInstructions();
  return NextResponse.json(
    { success: true, message: "Database instructions reset." },
    { status: 200, headers: CORS_HEADERS }
  );
}
