import { redirect } from "next/navigation";
import { isAuthenticated } from "@/lib/auth";
import Studio from "@/components/Studio";
import surahs from "@/data/surahs.json";
import reciters from "@/data/reciters.json";
import channels from "@/data/channels.json";

export const dynamic = "force-dynamic";

export default async function Home() {
  if (!(await isAuthenticated())) redirect("/login");

  return (
    <Studio
      surahs={surahs}
      reciters={reciters}
      channels={channels.channels}
    />
  );
}
