import { ScoredFile } from "@/lib/api";

/** Before downloading a cinema recording (backend core/cinema): say what it is and ask once more. */
export function confirmCinema(file: ScoredFile): boolean {
  if (!file.cinema) return true;
  const what = file.cinema === "video" ? "Obraz je natočený kamerou v kině."
    : file.cinema === "audio" ? "Český/slovenský zvuk je nahraný v kině (není to dabing z DVD / WEB)."
      : file.cinema === "suspect" ? "Film ještě nevyšel digitálně — soubor se jen tváří jako WEB / Blu-ray, nejspíš je z kina."
        : "Soubor je pravděpodobně z kina.";
  const why = file.cinema_reason ? `\n(${file.cinema_reason})` : "";
  return window.confirm(`⚠ Pravděpodobně záznam z kina\n\n${what}${why}\n\nOpravdu ho chceš stáhnout?`);
}
