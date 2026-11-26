// The terms of service and the privacy notice (PRD S14, IDEA-23). Shown before an account
// is created, from the agreement screen, and from Settings > Account at any time, so it has
// to render without a signed-in session. Both are agreed to by one tick, so they are one
// screen, one after the other.
//
// STILL A DRAFT, and it says so on the page. The wording is a legal statement in the
// operator's name and is Aditya's to write; what is here says, accurately, what the code
// really does, so that he is editing something true rather than starting from nothing.
// The banner is not a flag anyone has to remember to turn off: it is computed from OPERATOR
// below, so filling in the four facts that cannot be guessed from the code is what makes it
// stop calling itself a draft.
//
// Rules for changing this file:
//   - It says what JAP does TODAY, never what is planned.
//   - Every factual claim here is also a test in backend/tests/test_privacy_claims.py. If a
//     claim stops being true the test fails, which is the point: the notice cannot quietly
//     drift away from the code.
//   - Changing what it *says* means raising TERMS_VERSION in backend/auth/consent.py, which
//     asks everyone to agree again. Tidying the wording does not.

import {
  KEEP_BACKUPS_DAYS,
  KEEP_SCREENSHOTS_DAYS,
  KEEP_TRACES_DAYS,
  NOTICE_IS_A_DRAFT,
  OPERATOR,
  PLACEHOLDER,
} from '../legal'

function Para({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2>{title}</h2>
      {children}
    </section>
  )
}

function Operator({ field }: { field: keyof typeof OPERATOR }) {
  const value = OPERATOR[field]
  return value === PLACEHOLDER ? <b className="todo">[{field}]</b> : <>{value}</>
}

function Terms() {
  return (
    <>
      <h1>Terms of service</h1>
      <p className="muted">
        The agreement between you and <Operator field="name" />, who runs JAP. Read it with the privacy notice below; agreeing to one is agreeing to both.
      </p>

      <Para title="What JAP does">
        <p>
          JAP watches the public job boards of companies you follow, lists the jobs that match the profile you give it, and — when you ask it to — fills in and sends the employer&apos;s application form in your
          name. It is a tool that acts for you. It is not an employment agency, it does not represent any employer, and it has no relationship with the job boards it reads.
        </p>
      </Para>

      <Para title="Your account">
        <p>
          One account per person, with an email address you control and a password you keep to yourself. You are responsible for what is sent from your account. Tell us at <Operator field="email" /> if you
          think someone else is using it.
        </p>
        <p>You must be 18 or older to use JAP.</p>
      </Para>

      <Para title="You are the applicant, not JAP">
        <p>
          <b>Everything JAP sends goes out as you.</b> The employer receives an application from you; your name, your email address and your resume are on it. So what it says has to be true: the profile, the
          resume and the answers you save are yours, about you, and yours to stand behind. Do not use JAP to apply on behalf of somebody else.
        </p>
        <p>
          JAP asks separately before it does either of these, and does neither until you say so: sending an application in your name at all, and sending one without showing it to you first. You can withdraw
          either permission at any time in Settings, and nothing further is sent. An application already sent cannot be unsent.
        </p>
      </Para>

      <Para title="Answers written by a model">
        <p>
          JAP drafts answers to employers&apos; questions and cover letters with an AI model. A model can be wrong, and a draft can say something about you that is not so. In Review mode you see every answer
          before it is sent, and changing it is the point of that screen; in Automatic mode you have agreed that JAP may send a draft you have not read. Either way the application is yours, and so is what it
          claims.
        </p>
      </Para>

      <Para title="Paying for Plus">
        <p>
          Plus is a monthly subscription taken through Razorpay, on their page: JAP never sees your card or bank details. You can cancel at any time in Settings &gt; Your plan, and Plus lasts to the end of the
          month already paid for. If a renewal fails, Plus is kept for a few days while it is retried, and after that the account goes back to the free plan with its data untouched.
        </p>
      </Para>

      <Para title="What you may not do">
        <ul>
          <li>Apply as anyone but yourself, or give details that are not true.</li>
          <li>Work around the limits on your plan, or make accounts to get more of them.</li>
          <li>Use JAP to send bulk or junk applications, rather than to apply for jobs you want.</li>
          <li>Try to reach another user&apos;s data, or test the service&apos;s security without asking first at <Operator field="email" />.</li>
        </ul>
      </Para>

      <Para title="What JAP does not promise">
        <p>
          The free plan costs nothing, and Plus is paid for monthly through Razorpay; either way JAP is provided as it is. It does not promise you a job, an interview, or a reply. It does not promise that every matching job is found, that an application always arrives, or that the
          service is available at any particular time: a job board can change, an employer&apos;s form can defeat it, and the server can be down. Where the law allows, the operator is not liable for a job you
          did not get, an application that failed or was never sent, or anything that follows from what an application said.
        </p>
      </Para>

      <Para title="Stopping">
        <p>
          You can delete your account at any time in Settings &gt; Account, which removes your data at once. The operator may suspend or remove an account that breaks these terms, that is being used to apply
          as somebody else, or that puts the service at risk — and may stop running JAP altogether, with notice by email where that is possible.
        </p>
      </Para>

      <Para title="Changes, and the law these run under">
        <p>
          When these terms or the notice below change in a way that matters, you are asked to agree again the next time you sign in. These terms run under the law of <Operator field="jurisdiction" />.
        </p>
      </Para>
    </>
  )
}

function Privacy() {
  return (
    <>
      <h1>Privacy notice</h1>
      <p className="muted">What JAP holds about you, what it does with it, who else sees it, and how to get it back or have it deleted.</p>

      <Para title="Who holds your data">
        <p>
          The data controller is <Operator field="name" />, <Operator field="address" />. For anything in this notice — a copy of your data, a deletion, a correction, or a complaint — write to{' '}
          <Operator field="email" />, which is also the address for grievances under India&apos;s Digital Personal Data Protection Act.
        </p>
      </Para>

      <Para title="What JAP stores">
        <ul>
          <li>Your email address and password, which is kept only as a salted hash and is never readable.</li>
          <li>What you put on your profile: name, email, phone, address, links, the roles, skills and places you want.</li>
          <li>Every resume you upload, and the text taken out of it.</li>
          <li>The answers you give to employers&apos; questions, and cover letters written for you.</li>
          <li>Which companies you follow, which jobs matched you, and every application and its outcome.</li>
          <li>A screenshot and a recording of each form JAP fills in, as the record of what was sent.</li>
          <li>If you pay for Plus: which plan you are on, its state, when the paid month ends, and Razorpay&apos;s reference for the subscription. Never a card or bank detail.</li>
          <li>Sign-in records: when you signed in, from what kind of browser, and the security codes an employer emailed you.</li>
        </ul>
      </Para>

      <Para title="Why, and on what basis">
        <p>Finding jobs that match you, and applying for them. Nothing else. Your data is not sold, not shared with anyone but the employer you are applying to, and not used to advertise to you.</p>
        <table className="legal-table">
          <thead>
            <tr>
              <th>What for</th>
              <th>On what basis</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Running your account, finding jobs, preparing applications</td>
              <td>Performing the agreement you made by signing up</td>
            </tr>
            <tr>
              <td>Sending an application in your name, and sending one without showing it to you first</td>
              <td>Your consent, given separately for each, and withdrawable at any time</td>
            </tr>
            <tr>
              <td>Keeping the service up, stopping abuse, and counting what it costs to run</td>
              <td>The operator&apos;s legitimate interest in a service that works and is not abused</td>
            </tr>
          </tbody>
        </table>
      </Para>

      <Para title="What goes to an AI model">
        <p>
          JAP sends job descriptions, your resume text and the question being answered to an AI provider (OpenRouter) to read a job, draft an answer or write a cover letter.{' '}
          <b>Your name, email address, phone number, postal address and profile links are taken out before anything is sent</b>, and a request still carrying one is blocked rather than sent. Contact fields on an
          application form are filled in by JAP&apos;s own code, never by a model.
        </p>
        <p>
          JAP asks OpenRouter for providers that do not log or train on what is sent, and a request that cannot be served by one is not sent at all. Embeddings, which is how two postings are recognised as the
          same job, are computed on the operator&apos;s own server and go nowhere.
        </p>
      </Para>

      <Para title="Who else your data reaches">
        <table className="legal-table">
          <thead>
            <tr>
              <th>Who</th>
              <th>What reaches them</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>The employer you apply to, through their job board (Greenhouse, Lever or Ashby)</td>
              <td>Your application: name, contact details, resume, answers and cover letter</td>
            </tr>
            <tr>
              <td>OpenRouter, and the model provider it routes each request to</td>
              <td>Job text, and your resume text and answers with contact details removed</td>
            </tr>
            <tr>
              <td>Cloudflare</td>
              <td>Every request to the site, and the sign-up &quot;prove you are human&quot; check</td>
            </tr>
            <tr>
              <td>The operator&apos;s email provider</td>
              <td>Email to you, and the employers&apos; security-code emails JAP reads</td>
            </tr>
            <tr>
              <td>Google, only if you choose to sign in with Google</td>
              <td>That you signed in; Google tells JAP your email address and that it is verified</td>
            </tr>
            <tr>
              <td>Razorpay, only if you pay for Plus</td>
              <td>JAP sends Razorpay nothing about you but an account id; your name, email and card you type into Razorpay&apos;s own page</td>
            </tr>
            <tr>
              <td>The company hosting the server, and Sentry if error reporting is on</td>
              <td>The disk the database sits on; scrubbed error reports with no personal data in them</td>
            </tr>
          </tbody>
        </table>
        <p>
          Some of these are outside the country you live in, including the United States. Where that is a transfer out of the European Economic Area it is made under the standard contractual clauses in each
          provider&apos;s own data processing terms.
        </p>
      </Para>

      <Para title="How long it is kept">
        <ul>
          <li>Your account and everything on it: until you delete it.</li>
          <li>The recording of a form being filled in: {KEEP_TRACES_DAYS} days.</li>
          <li>The screenshot of what was sent, which is the evidence of your application: {KEEP_SCREENSHOTS_DAYS} days.</li>
          <li>Database backups: {KEEP_BACKUPS_DAYS} days, after which a deleted account is gone from those too.</li>
          <li>What each AI call cost: kept, with nothing in it that points at you.</li>
        </ul>
      </Para>

      <Para title="Your data, back or gone">
        <p>
          <b>Download my data</b> gives you a zip of everything JAP holds about you: your profile, resumes, answers, applications, searches, the jobs that matched you and every file. <b>Delete my account</b>{' '}
          removes all of it, and the files with it, at once and for good — there is no undo, and nothing is kept to restore from. Both are in Settings &gt; Account, and both are there even if you decide
          not to agree to this notice.
        </p>
        <p>
          One thing is kept after a deletion: the record of what each AI call cost, with nothing in it that points at you — no name, no text, only the purpose, the model and the amount. It is what the running cost
          of the service is counted from.
        </p>
        <p>
          You can also correct anything on your profile yourself, withdraw either applying permission in Settings, and object to the service being run on your data by deleting the account. Because both of the
          main ones are buttons, they happen immediately rather than within the month the law allows. If you would rather ask a person, write to <Operator field="email" />.
        </p>
        <p>
          If you think your data is being handled wrongly, you can complain to a data protection authority: in India the Data Protection Board, and in the European Economic Area the supervisory authority where
          you live.
        </p>
      </Para>

      <Para title="Who can see it">
        <p>
          No other user can see anything of yours: every page and every background job works for one account, and the database refuses a query that reaches past it. The administrator can see that your account
          exists, when it was made, when you last signed in and what your AI use has cost — never your resume, your answers or your jobs.
        </p>
      </Para>

      <Para title="Decisions made without a person">
        <p>
          JAP decides by itself which jobs match you, and in Automatic mode it decides to apply without asking. Neither decision has a legal effect on you — not applying for a job is not a decision about you —
          and you can change what it matches on, review every application instead, or turn Automatic mode off, at any time.
        </p>
      </Para>

      <Para title="Cookies">
        <p>
          One cookie, which keeps you signed in. It is not used to track you, there is no advertising and no analytics anywhere in JAP, and nothing is shared with a third party for either. That is why there is
          no cookie banner: the only cookie is the one the site cannot work without.
        </p>
      </Para>

      <Para title="How it is kept safe">
        <p>
          Passwords are stored only as salted hashes. Your rows carry your account id and the database itself refuses to return anyone else&apos;s, so a mistake in the code is caught a second time by Postgres.
          Personal details are stripped before anything reaches an AI model. Logs are scrubbed of addresses, phone numbers, keys and codes, and answers, resume text and cover letters are never written to them
          at all. Database backups are encrypted to a key held off the server.
        </p>
        <p>If your data is ever exposed, you will be told, and so will the authorities the law requires, as quickly as the facts allow.</p>
      </Para>

      <Para title="Where it is kept">
        <p>JAP runs on one server the operator rents, behind Cloudflare. The database and the files are on that server; the backups are encrypted copies of it.</p>
      </Para>

      <Para title="When this changes">
        <p>
          A change that matters takes everyone back through the agreement screen the next time they sign in, so you are never held to a notice you have not seen. Write to <Operator field="email" /> with any
          question about this one.
        </p>
      </Para>
    </>
  )
}

export default function Legal() {
  return (
    <div className="notice">
      {NOTICE_IS_A_DRAFT && (
        <div className="note-box warn">
          <b>Draft.</b> These are not finalised. They describe exactly what JAP does today, but the operator&apos;s own details are not filled in yet, so this is not a legal statement you should rely on.
        </div>
      )}
      <Terms />
      <hr />
      <Privacy />
    </div>
  )
}
