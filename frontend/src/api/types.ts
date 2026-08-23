/**
 * API types. These mirror the backend Pydantic schemas — keep them in sync.
 * Generate from /api/openapi.json with openapi-typescript if you prefer.
 */

export type CoverageTier = 'meets_all' | 'one_short' | 'multiple_gaps'
export type Verdict = 'met' | 'partial' | 'not_met'
export type EvidenceBand =
  | 'direct_with_context' | 'direct' | 'skills_list'
  | 'strong_proxy' | 'hedged' | 'none'
export type Necessity = 'must_have' | 'nice_to_have'
export type RequirementWeight = 'High' | 'Medium' | 'Low'
export type DecisionAction = 'shortlist' | 'reject' | 'interview' | 'on_hold' | 'reset'
export type BatchStatus =
  | 'queued' | 'extracting' | 'matching' | 'verifying'
  | 'scoring' | 'completed' | 'failed' | 'cancelled'

export interface User {
  id: string
  email: string
  full_name: string
  role: 'recruiter' | 'admin'
  job_title?: string | null
  organization_id: string
}

export interface AuthResponse {
  access_token: string
  refresh_token: string
  expires_in: number
  user: User
}

export interface Requirement {
  id: string
  text: string
  kind: string
  necessity: Necessity
  weight: RequirementWeight
  aliases: string[]
  canonical_skill: string | null
  min_years: number | null
  display_order: number
  recruiter_edited: boolean
  source_span: string | null
}

export interface Job {
  id: string
  title: string
  raw_text: string
  status: string
  seniority: string | null
  min_years_experience: number | null
  category_weights: CategoryWeights
  requirements: Requirement[]
  must_have_count: number
  nice_to_have_count: number
}

export interface CategoryWeights {
  skills: number
  experience: number
  projects: number
  education: number
  certifications: number
}

export interface CandidateRow {
  candidate_id: string
  reference: string
  display_name: string
  title: string | null
  experience_years: number | null
  rank: number
  must_haves_met: number
  must_haves_total: number
  coverage_tier: CoverageTier
  tier_label: string
  match_level: string
  final_score: number
  missing_requirements: string[]
  decision_status: string
  scorer_version: string
  blind: boolean
  identity?: { full_name: string | null; email: string | null; phone: string | null }
}

export interface ResultsResponse {
  items: CandidateRow[]
  total: number
  page: number
  page_size: number
  tier_counts: Record<CoverageTier, number>
  blind_screening: boolean
  scorer_version: string
  disclaimer: string
}

export interface CoverageItem {
  requirement_id: string
  text: string
  necessity: Necessity
  weight: RequirementWeight
  verdict: Verdict
  ai_verdict?: Verdict
  evidence_band: EvidenceBand
  evidence_grade: number
  quote: string | null
  page?: number | null
  char_start?: number | null
  char_end?: number | null
  absence_statement: string | null
  reasoning?: string | null
  confidence?: number
  confidence_band: 'high' | 'medium' | 'low'
  confidence_label?: string
  confidence_description?: string
  method?: string
  model_version?: string | null
  quote_validated?: boolean
  search_terms: string[]
  override?: { verdict: Verdict; reason: string | null; at: string | null } | null
}

export interface CandidateDetail {
  candidate_id: string
  reference: string
  display_name: string
  title: string | null
  experience_years: number | null
  decision_status: string
  blind: boolean
  coverage: CoverageItem[]
  gaps: { requirement_id: string; text: string; absence_statement: string | null }[]
  questions: {
    id: string
    requirement_id: string
    question: string
    rationale: string | null
    added_to_interview: boolean
  }[]
  decision_trail: { action: string; note: string | null; coverage: string | null; at: string }[]
  disclaimer: string
  score?: {
    must_haves_met: number
    must_haves_total: number
    coverage_tier: CoverageTier
    tier_label: string
    match_level: string
    final_score: number
    must_have_score: number
    nice_to_have_bonus: number
    rank: number
    scorer_version: string
    category_weights: CategoryWeights
    tier_note: string
  }
  identity?: { full_name: string | null; email: string | null; phone: string | null }
}

export interface QuarantineItem {
  document_id: string
  candidate_id: string
  filename: string
  status: string
  reason: string
  extraction_confidence: number | null
  signals: Record<string, unknown>
  can_retry: boolean
  can_ocr: boolean
}

export interface ScreeningStatus {
  id: string
  name: string
  status: BatchStatus
  job: { id: string; title: string }
  total_documents: number
  processed_count: number
  quarantined_count: number
  failed_count: number
  progress: number
  blind_screening: boolean
  category_weights: CategoryWeights
  scorer_version: string
  error_message: string | null
  started_at: string | null
  completed_at: string | null
}

export interface DashboardData {
  kpis: {
    active_jobs: number
    candidates_screened: number
    shortlisted: number
    interviews: number
    average_must_have_coverage: number | null
  }
  pool: Record<CoverageTier, number>
  common_gaps: { requirement: string; missing_count: number }[]
  recent_screenings: {
    id: string
    job_title: string
    name: string
    status: string
    total: number
    quarantined: number
    created_at: string
  }[]
}

export interface ApiError {
  code: string
  message: string
  request_id?: string
  fields?: { field: string; message: string }[]
}
