// Presentation metadata for the web client: display order, canonical value
// orders, and illustrative question wording.
//
// This is deliberately NOT the list of measures/dimensions or which are numeric.
// The client reads those from GET /meta (sourced from the allowlist) so it
// consumes the API rather than holding a second copy that could drift. What
// lives here is purely presentational and not owned by the API:
//   - the rail display order for measures and dimensions,
//   - the canonical value order for ordinal axes (the API alphabetizes; the
//     client re-imposes order before render),
//   - placeholder question wording (the CSV carries no question text; swap in
//     the real survey items if you have them).
//
// Apostrophes are ASCII to match the ingest cleaning step (curly U+2019 is
// normalized to "'" before storage); the client sort normalizes both sides, so
// a curly/straight mismatch can never silently drop a value to the end.
window.SURVEY_PRESENTATION = {
  measureOrder: ["q1_rating", "q2_rating", "q4_rating", "sentiment_label"],
  dimensionOrder: ["state", "gender", "education_level", "income", "age_bucket"],
  descriptions: {
    q1_rating: "AI will benefit me personally",
    q2_rating: "AI is developing too quickly",
    q4_rating: "I trust AI with important decisions",
    sentiment_label: "Overall sentiment toward AI",
  },
  canonical: {
    q1_rating: ["1", "2", "3", "4", "5"],
    q2_rating: ["1", "2", "3", "4", "5"],
    q4_rating: ["1", "2", "3", "4", "5"],
    sentiment_label: ["Negative", "Neutral", "Positive"],
    education_level: [
      "High School",
      "Some College",
      "Associate Degree",
      "Bachelor's Degree",
      "Master's Degree",
      "Doctorate",
    ],
    income: ["Low", "Lower-Middle", "Upper-Middle", "High"],
    age_bucket: ["18-29", "30-44", "45-59", "60+"],
  },
};
