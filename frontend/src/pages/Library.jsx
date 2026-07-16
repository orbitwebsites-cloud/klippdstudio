import LibraryPanel from "@/components/LibraryPanel";

export default function Library() {
    return (
        <main className="min-h-[calc(100vh-72px)] px-4 sm:px-6 md:px-16 py-8 md:py-12">
            <div className="max-w-7xl mx-auto">
                <LibraryPanel standalone />
            </div>
        </main>
    );
}
