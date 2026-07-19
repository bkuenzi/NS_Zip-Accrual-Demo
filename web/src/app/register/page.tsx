"use client";

import { RegisterTable } from "@/components/register-table";
import { ReviewQueue } from "@/components/review-queue";

export default function RegisterPage() {
  return (
    <div className="flex flex-col gap-4">
      <ReviewQueue />
      <RegisterTable />
    </div>
  );
}
