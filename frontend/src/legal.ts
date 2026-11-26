// The facts the terms and the privacy notice are built from (PRD S14, IDEA-23).
//
// Separate from pages/LegalPage.tsx so that file exports a component and nothing else
// (react-refresh), and so these can be read on their own — backend/tests/test_privacy_claims.py
// checks them against the settings the server really runs on.

export const PLACEHOLDER = 'TO BE FILLED IN'

// The four things no amount of reading the code can tell you. They are a statement about a
// real person, Aditya's own, and they are filled in. While any one of them is PLACEHOLDER
// again, the terms and the notice call themselves a draft, on the page, where a reader can see
// it. The address is the state only for now; a full postal address goes in before Plus goes
// on sale (launch-plan.md, stage 1).
export const OPERATOR = {
  name: 'Aditya', // the person who runs JAP, as the data controller
  address: 'Rajasthan, India', // where the controller is; a full postal address before Plus
  email: 'privacy@tutorial.monster', // where privacy requests and grievances go
  jurisdiction: 'India', // whose courts and whose law the terms run under
}

// Computed, never hand-set: a flag someone has to remember to turn off is one that gets
// turned off while the operator's details are still placeholders.
export const NOTICE_IS_A_DRAFT = Object.values(OPERATOR).some((v) => v === PLACEHOLDER)

// Stated in "How long it is kept", and enforced by JAP_KEEP_TRACES_DAYS and
// JAP_KEEP_SCREENSHOTS_DAYS in backend/config.py and KEEP_DAYS in pipeline/backup.py.
// test_privacy_claims.py fails if the two ever disagree, so changing a retention period
// means changing what users were told.
export const KEEP_TRACES_DAYS = 30
export const KEEP_SCREENSHOTS_DAYS = 180
export const KEEP_BACKUPS_DAYS = 14
