/** Parse viewport media-query width boundaries without coupling container queries to them. */
export function findDisallowedViewportWidths(source, allowed) {
  const violations = [];
  for (const mediaMatch of source.matchAll(/@media\s*([^\{]*)\{/g)) {
    for (const widthMatch of mediaMatch[1].matchAll(/(?:min|max)-width\s*:\s*(\d+)px/g)) {
      const value = Number(widthMatch[1]);
      if (!allowed.has(value)) {
        violations.push({
          value,
          index:
            (mediaMatch.index ?? 0) +
            mediaMatch[0].indexOf(mediaMatch[1]) +
            (widthMatch.index ?? 0),
        });
      }
    }
  }
  return violations;
}
