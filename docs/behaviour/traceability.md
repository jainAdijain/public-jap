# Traceability

One row per requirement in [requirements.md](../intent/requirements.md): the code that does it,
the tests that prove it, and its state. **Proved** means a named test fails if the requirement
breaks. **Gap** means nothing automated proves it yet, and says why.

`infra/scripts/check_docs.py` (run by `make test-agents` and CI) fails when a requirement has no
row here, when a named test does not exist, or when a row names no test and is not a gap. Tests
live in `backend/tests/`.

## Accounts and sign-in

| Requirement | Code | Tests | State |
| --- | --- | --- | --- |
| AUTH-1 Sign-up with a confirmation link | `backend/api/auth.py`, `backend/auth/` | `test_sign_up.py::test_sign_up_verify_and_an_empty_workspace`, `test_sign_up.py::test_sign_up_says_the_same_for_a_taken_address`, `test_sign_up.py::test_a_verify_link_expires` | Proved |
| AUTH-2 First account is the admin | `backend/api/auth.py` | `test_auth.py::test_first_account_is_the_admin_and_needs_no_email_check`, `test_auth.py::test_admin_email_setting_limits_the_first_account` | Proved |
| AUTH-3 Tries limited, lockout | `backend/auth/` (rate limits) | `test_sign_up.py::test_repeated_tries_are_refused`, `test_sign_up.py::test_the_address_comes_from_the_trusted_header_only`, `test_auth.py::test_five_wrong_passwords_lock_sign_in` | Proved |
| AUTH-4 Sessions | `backend/auth/` | `test_auth.py::test_cookie_is_httponly_lax_and_host_only`, `test_auth.py::test_idle_and_old_sessions_end`, `test_auth.py::test_sign_out_everywhere` | Proved |
| AUTH-5 Two-step sign-in | `backend/auth/` | `test_auth.py::test_two_step_sign_in`, `test_auth.py::test_recovery_code_works_once`, `test_auth.py::test_totp_matches_rfc_6238_vectors` | Proved |
| AUTH-6 Password reset | `backend/api/auth.py` | `test_auth.py::test_password_reset_by_email`, `test_auth.py::test_reset_link_expires_and_ends_sessions` | Proved |
| AUTH-7 Google sign-in | `backend/auth/oidc.py` | `test_google.py::test_google_is_not_there_until_it_is_configured`, `test_google.py::test_an_address_is_only_joined_up_when_google_vouches_for_it`, `test_google.py::test_a_callback_that_did_not_start_here_is_refused` | Proved; never tried against Google itself |
| AUTH-8 Session on every route but the open set | `backend/main.py`, `backend/auth/deps.py` | `test_auth.py::test_every_route_but_health_and_sign_in_needs_a_session`, `test_auth.py::test_file_links_need_a_session` | Proved |
| AUTH-9 Cross-site writes refused | `backend/auth/` (guards) | `test_auth.py::test_requests_from_other_sites_are_refused`, `test_auth.py::test_a_write_from_another_site_without_an_origin_is_refused` | Proved |
| AUTH-10 Pause sign-up, turn an account off | `backend/api/admin.py` | `test_sign_up.py::test_admin_pauses_sign_up_and_turns_an_account_off`, `test_sign_up.py::test_a_turned_off_account_stops_searching`, `test_auth.py::test_a_turned_off_account_cannot_sign_in` | Proved |

## One user's data kept from another's

| Requirement | Code | Tests | State |
| --- | --- | --- | --- |
| TEN-1 Row-level security on every private table | `backend/database/tenant.py`, migration `0013_rls` | `test_tenancy.py::test_every_private_table_has_a_user_and_a_policy`, `test_tenancy.py::test_no_user_in_scope_means_no_rows`, `test_tenancy.py::test_private_code_without_a_user_fails_loudly`, `test_schema.py::test_a_private_row_needs_a_user` | Proved |
| TEN-2 Another user's id is 404 | every router in `backend/api/` | `test_tenancy.py::test_another_users_ids_answer_not_found_on_every_route` | Proved |
| TEN-3 Shared facts, private lists | `backend/pipeline/matching.py`, `user_listing` | `test_tenancy.py::test_one_fetch_two_lists`, `test_tenancy.py::test_lists_hold_only_my_own`, `test_tenancy.py::test_my_unfollow_hide_and_apply_leave_your_list_alone` | Proved |
| TEN-4 Files per user | `backend/pipeline/storage.py` | `test_tenancy.py::test_files_are_kept_per_user` | Proved |
| TEN-5 Background work as the event's user | `backend/observability/inngest_mw.py` | `test_tenancy.py::test_background_work_is_the_events_user` | Proved |
| TEN-6 Cross-user counts, never rows | SQL functions `jap_last_submit`, `jap_applied_today` | `test_pacing.py::test_the_service_wide_counts_do_not_leak_other_peoples_rows` | Proved |
| TEN-7 Security codes by recipient address | `backend/pipeline/apply/security_code.py` | `test_code_routes.py::test_each_account_gets_its_own_address`, `test_code_routes.py::test_inbound_mail_gives_the_code_to_the_right_account`, `test_code_routes.py::test_inbound_mail_for_a_stranger_is_refused` | Proved |
| TEN-8 The admin never sees user content | `backend/api/admin.py` | none | Gap: no test checks that the admin screens leave out what users wrote |

## Agreements

| Requirement | Code | Tests | State |
| --- | --- | --- | --- |
| CONSENT-1 Terms at sign-up, asked again | `backend/auth/consent.py` | `test_consent.py::test_sign_up_needs_the_tick_and_the_notice_it_showed`, `test_consent.py::test_a_notice_nobody_agreed_to_asks_before_anything_else`, `test_consent.py::test_a_newer_notice_asks_an_agreed_account_again` | Proved |
| CONSENT-2 Opt-in before applying | `backend/auth/consent.py`, `backend/pipeline/apply/flow.py` | `test_consent.py::test_nothing_is_prepared_without_the_opt_in`, `test_consent.py::test_taking_the_opt_in_back_stops_an_attempt_that_is_waiting` | Proved |
| CONSENT-3 Automatic mode needs its own agreement | `backend/auth/consent.py` | `test_consent.py::test_automatic_mode_needs_its_own_agreement`, `test_consent.py::test_taking_back_the_wider_opt_in_takes_the_narrower_one_with_it`, `test_consent.py::test_a_run_in_automatic_mode_picks_nothing_without_the_second_opt_in` | Proved |

## A person's data back or gone

| Requirement | Code | Tests | State |
| --- | --- | --- | --- |
| DATA-1 Download my data | `backend/api/account.py` | `test_account_delete.py::test_export_holds_everything_of_mine_and_nothing_of_yours`, `test_account_delete.py::test_every_private_table_is_exported_and_deleted`, `test_code_routes.py::test_the_code_is_left_out_of_an_export` | Proved |
| DATA-2 Delete my account | `backend/pipeline/erasure.py` | `test_account_delete.py::test_delete_takes_everything_of_mine_and_nothing_of_yours`, `test_account_delete.py::test_delete_asks_for_the_password_and_the_address`, `test_account_delete.py::test_export_and_delete_work_without_agreeing_to_a_new_notice`, `test_billing.py::test_deleting_an_account_stops_the_money_first` | Proved |
| DATA-3 Retention | `backend/pipeline/retention.py` | `test_retention.py::test_old_traces_go_and_recent_ones_and_documents_stay`, `test_retention.py::test_every_users_folder_is_swept_not_only_the_first`, `test_observability.py::test_cleanup_keeps_30_days` | Proved |
| DATA-4 Every notice claim is a test | `frontend/src/pages/LegalPage.tsx`, `frontend/src/legal.ts` | `test_privacy_claims.py::test_every_claim_is_still_in_the_notice`, `test_privacy_claims.py::test_razorpay_is_sent_an_account_id_and_nothing_else`, `test_privacy_claims.py::test_untestable_claims_are_listed_with_a_reason` | Proved |

## Models and logs

| Requirement | Code | Tests | State |
| --- | --- | --- | --- |
| PRIV-1 No personal data to a model | `backend/llm/redact.py`, `backend/llm/call.py` | `test_redact.py::test_every_spelling_is_redacted_and_guarded`, `test_llm_call.py::test_guard_blocks_and_nothing_is_sent`, `test_enrich.py::test_direct_fields_are_not_sent_for_extraction` | Proved |
| PRIV-2 One file talks to OpenRouter | `backend/llm/client.py` | `test_isolation.py::test_only_the_client_talks_to_openrouter`, `test_llm_call.py::test_call_redacts_logs_and_validates`, `test_llm_call.py::test_every_call_caps_max_tokens` | Proved |
| PRIV-3 Logs scrubbed | `backend/observability/scrub.py` | `test_observability.py::test_scrub_removes_secret_shapes_and_keeps_ids`, `test_observability.py::test_scrub_value_drops_personal_fields`, `test_observability.py::test_sentry_events_are_scrubbed` | Proved |

## Jobs

| Requirement | Code | Tests | State |
| --- | --- | --- | --- |
| JOBS-1 Follow a board; fetch only job sites | `backend/sources/` | `test_sources.py::test_detect_url_checks_the_board`, `test_sources.py::test_detect_name_tries_each_ats`, `test_hosting.py::test_a_redirect_off_the_ats_is_refused`, `test_hosting.py::test_a_board_cannot_send_jap_anywhere_else` | Proved |
| JOBS-2 One row per job | `backend/pipeline/dedup.py`, `backend/pipeline/canonical.py` | `test_dedup_qa.py::test_dedup_qa_set`, `test_dedup.py::test_requisition_id_joins_different_titles`, `test_dedup.py::test_merge_and_split_are_remembered` | Proved |
| JOBS-3 Matching; skills never hide | `backend/pipeline/matching.py` | `test_matching.py::test_title_or_location_lists_newest_first`, `test_matching.py::test_skills_never_hide_a_job`, `test_matching.py::test_rule_change_refilters_without_a_fetch` | Proved |
| JOBS-4 Unfollow deletes nothing | `backend/pipeline/unfollow.py` | `test_unfollow.py::test_unfollow_closes_postings_and_jobs_and_deletes_nothing`, `test_unfollow.py::test_follow_again_reopens_and_updates_the_same_rows` | Proved |
| JOBS-5 A description read once, only when wanted | `backend/functions/enrich_posting.py` | `test_tenancy.py::test_a_job_nobody_wants_is_not_read`, `test_enrich.py::test_extract_fills_fields_once` | Proved |
| JOBS-6 Daily search in each account's zone | `backend/functions/triggers.py` | `test_triggers.py::test_the_daily_search_goes_by_each_accounts_own_zone` | Proved |
| JOBS-7 One search per account, side by side | `backend/functions/run_pipeline.py` | `test_triggers.py::test_a_queued_run_waits_for_that_users_own`, `test_triggers.py::test_another_users_search_does_not_hold_mine_up` | Proved |
| JOBS-8 A fresh board is reused | `backend/pipeline/ingest.py` | `test_ingest.py::test_a_board_someone_just_fetched_is_not_fetched_again`, `test_ingest.py::test_a_first_fetch_and_a_failed_one_are_never_fresh` | Proved |

## Applying

| Requirement | Code | Tests | State |
| --- | --- | --- | --- |
| APPLY-1 Review mode | `backend/pipeline/apply/` | `test_apply.py::test_apply_now_stops_at_ready_for_review`, `test_apply.py::test_facts_only_i_know_go_to_needs_input_and_the_answer_bank`, `test_apply.py::test_captcha_and_missing_files_block` | Proved |
| APPLY-2 Submit at most once | `backend/pipeline/apply/flow.py`, trigger on `application` | `test_apply.py::test_submit_never_presses_twice`, `test_apply.py::test_unconfirmed_submit_fails_and_unpressed_submit_retries`, `test_schema.py::test_one_applied_per_listing_and_applied_is_frozen` | Proved |
| APPLY-3 Sending off never submits | `backend/pipeline/apply/flow.py` | `test_apply.py::test_sending_off_never_opens_the_form` | Proved |
| APPLY-4 Automatic mode and the skills bar | `backend/pipeline/apply/flow.py` | `test_apply.py::test_automatic_mode_picks_only_at_or_above_the_skills_bar`, `test_hardening.py::test_automatic_mode_applies_unless_unclear` | Proved |
| APPLY-5 Cover letter only when required | `backend/pipeline/cover_letter.py` | `test_cover_letter.py::test_mandatory_letter_is_written_checked_and_signed_locally`, `test_cover_letter.py::test_optional_letter_and_no_field_get_no_letter`, `test_cover_letter.py::test_an_untrue_claim_regenerates_once` | Proved |
| APPLY-6 Pacing | `backend/pipeline/apply/flow.py` | `test_apply.py::test_cap_and_gap`, `test_pacing.py::test_a_gap_is_kept_between_two_applications_to_one_site`, `test_pacing.py::test_the_service_stops_at_its_own_daily_ceiling`, `test_pacing.py::test_a_paused_account_sends_nothing` | Proved |
| APPLY-7 One browser per account | `backend/functions/apply_listing.py` | `test_pacing.py::test_applying_is_one_browser_per_user_under_a_service_ceiling` | Proved |

## Limits, AI budgets and paying

| Requirement | Code | Tests | State |
| --- | --- | --- | --- |
| BILL-1 `limits_for` decides | `backend/pipeline/limits.py` | `test_billing.py::test_the_admins_numbers_for_one_account_beat_the_plan`, `test_billing.py::test_a_user_can_still_hold_themselves_below_their_plan`, `test_billing.py::test_a_plan_that_no_longer_exists_falls_back_to_free`, `test_tenancy.py::test_the_free_plan_caps_companies_followed` | Proved |
| BILL-2 Three AI budgets | `backend/llm/spend.py` | `test_tenancy.py::test_one_user_at_their_limit_stops_only_them`, `test_tenancy.py::test_the_service_budget_and_the_total_limit`, `test_llm_call.py::test_spend_cap_stops_calls` | Proved |
| BILL-3 Your own key | `backend/llm/secrets.py` | `test_byok.py::test_a_sealed_key_opens_only_for_its_owner`, `test_byok.py::test_the_hint_is_only_the_last_four`, `test_byok.py::test_my_own_key_is_used_and_billed_to_me_not_to_jap` | Proved |
| BILL-4 Signed webhook, once, never backwards | `backend/api/webhooks.py`, `backend/billing/service.py` | `test_billing.py::test_an_unsigned_webhook_is_not_found_and_changes_nothing`, `test_billing.py::test_the_same_delivery_twice_is_handled_once`, `test_billing.py::test_a_webhook_that_arrives_late_does_not_undo_a_newer_one`, `test_billing.py::test_the_webhook_is_off_when_no_provider_is_configured` | Proved; never run against Razorpay |
| BILL-5 Checkout and grace | `backend/api/billing.py` | `test_billing.py::test_checkout_sends_the_browser_to_the_providers_page`, `test_billing.py::test_checkout_is_refused_while_the_service_is_not_selling`, `test_billing.py::test_a_failed_renewal_keeps_the_plan_to_the_end_of_the_period` | Proved |

## Running it

| Requirement | Code | Tests | State |
| --- | --- | --- | --- |
| OPS-1 `/api/inngest` only on the worker | `backend/main.py` | `test_hosting.py::test_the_api_process_does_not_mount_the_inngest_endpoint`, `test_auth.py::test_inngest_endpoint_refuses_proxied_requests` | Proved |
| OPS-2 Health and alerts | `backend/observability/` | `test_health.py::test_health_all_ok`, `test_health.py::test_health_reports_ollama_down`, `test_observability.py::test_health_alerts_are_sent_once` | Proved |
| OPS-3 Daily dumps, encrypted | `backend/pipeline/backup.py` | `test_hardening.py::test_backup_files`, `test_hardening.py::test_backup_encrypted_to_a_key_the_server_does_not_hold`, `test_hardening.py::test_backup_refuses_plaintext_when_age_is_missing` | Proved; Gap: no restore drill has been done |
| OPS-4 Release by tag | `.github/workflows/release.yml`, `infra/scripts/deploy.sh` | none | Gap: no `v*` tag has ever been pushed |
| OPS-5 Version from git tags | not built | none | Gap: not built; the version is still edited in four files by hand (`AGENTS.md`, Releases) |
