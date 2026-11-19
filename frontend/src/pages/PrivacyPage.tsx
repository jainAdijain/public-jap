// The terms and privacy notice (PRD S14). Shown before an account is created, from the
// agreement screen, and from Settings > Account at any time, so it has to render without a
// signed-in session.
//
// DRAFT. The wording is a statement in the operator's name and is Aditya's to write; this
// is a placeholder that says what JAP actually does, for him to replace. The banner at the
// top says so, and stays until he removes it. Changing what the notice *says* means raising
// TERMS_VERSION in backend/auth/consent.py, which asks everyone again.
//
// It says what JAP does *today*, never what is planned: "Your data, back or gone" goes back
// to naming Export and Delete my account when those are in Settings > Account (IDEA-5).

export const NOTICE_IS_A_DRAFT = true

function Para({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2>{title}</h2>
      {children}
    </section>
  )
}

export default function PrivacyNotice() {
  return (
    <div className="notice">
      {NOTICE_IS_A_DRAFT && (
        <div className="note-box warn">
          <b>Draft.</b> This notice has not been finalised. It describes what JAP does today, but it is not yet a legal statement you should rely on.
        </div>
      )}
      <h1>Terms and privacy notice</h1>
      <p className="muted">JAP (Job Application Pipeline) finds jobs that match you and applies for them. This says what it holds about you, what it does with it, and what you can ask for back.</p>

      <Para title="What JAP stores">
        <ul>
          <li>Your email address and password, which is kept only as a salted hash and is never readable.</li>
          <li>What you put on your profile: name, email, phone, address, links, the roles, skills and places you want.</li>
          <li>Every resume you upload, and the text taken out of it.</li>
          <li>The answers you give to employers' questions, and cover letters written for you.</li>
          <li>Which companies you follow, which jobs matched you, and every application and its outcome.</li>
          <li>A screenshot and a recording of each form JAP fills in, as the record of what was sent.</li>
        </ul>
      </Para>

      <Para title="What it is used for">
        <p>Finding jobs that match you, and applying for them. Nothing else. Your data is not sold, not shared with anyone but the employer you are applying to, and not used to advertise to you.</p>
      </Para>

      <Para title="What goes to an AI model">
        <p>
          JAP sends job descriptions, your resume text and the question being answered to an AI provider (OpenRouter) to read a job, draft an answer or write a cover letter.{' '}
          <b>Your name, email address, phone number, postal address and profile links are taken out before anything is sent</b>, and a request still carrying one is blocked rather than sent. Contact fields on an
          application form are filled in by JAP's own code, never by a model.
        </p>
      </Para>

      <Para title="JAP applies in your name">
        <p>
          When you agree to it, JAP opens an employer's application form, fills it in with your details and your resume, and presses Submit as you. The employer receives an application from you, not from JAP. You
          agree to this once before the first application, and again, separately, before JAP is allowed to send one without showing it to you first.
        </p>
        <p>You can take either agreement back at any time in Settings. Nothing further is sent after that; applications already sent cannot be unsent.</p>
      </Para>

      <Para title="Your data, back or gone">
        <p>
          Ask the operator and you will be sent everything JAP holds about you — your profile, resumes, answers, applications, searches and files — or have all of it deleted, the files with it, for good.
          Taking that back by yourself, from Settings &gt; Account, is being built.
        </p>
        <p>
          One thing is kept after a deletion: the record of what each AI call cost, with nothing in it that points at you — no name, no text, only the purpose, the model and the amount. It is what the running cost
          of the service is counted from.
        </p>
      </Para>

      <Para title="Who can see it">
        <p>
          No other user can see anything of yours: every page and every background job works for one account, and the database refuses a query that reaches past it. The administrator can see that your account
          exists, when it was made, when you last signed in and what your AI use has cost — never your resume, your answers or your jobs.
        </p>
      </Para>

      <Para title="Where it is kept">
        <p>JAP runs on the operator's own hardware. The database and the files are not on a public cloud. Email is sent through the operator's mailbox.</p>
      </Para>
    </div>
  )
}
