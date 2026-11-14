import Table from "./table";

export default async function TablePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <Table roomId={id} />;
}
