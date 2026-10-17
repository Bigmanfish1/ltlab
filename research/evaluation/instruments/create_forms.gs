/**
 * Builds the two counterbalanced LTLab evaluation forms.
 *
 * Group 1 takes test Form A before the tool phase and Form B after it.
 * Group 2 takes them the other way round. Everything else is identical.
 *
 * HOW TO RUN
 *   1. Go to script.google.com, New project, paste this file in, replace Code.gs.
 *   2. Run createBothForms once. Authorise when prompted: creating a form needs
 *      the https://www.googleapis.com/auth/forms scope, so the consent screen
 *      will ask to "see, edit, create and delete all your Google Forms". The
 *      script only creates the two forms below.
 *   3. The edit and share links for both forms are printed to the execution log
 *      (View, Logs, or Execution log). Paste them into allocation_list.md.
 *   4. Work through the manual settings checklist at the end of this file.
 *
 * Running it twice creates duplicate forms. If you need to start over, delete the
 * previous pair from Google Drive first.
 *
 * Question content comes from materials_quiz.md and materials_survey.md.
 * Correct options were verified against the app's model-checking engine.
 */

// ---------------------------------------------------------------- content

var TOOL_URL = 'https://ltlab-87262955263.us-central1.run.app';

// Matches apps/accounts/participant.py (P- plus 6 hex chars). Lowercase is accepted and
// uppercased at analysis, so a correct code typed in lower case is not rejected.
var PARTICIPANT_CODE_PATTERN = '[Pp]-[0-9A-Fa-f]{6}';

var CONSENT_TEXT = [
  'You are invited to take part in the evaluation of LTLab, an educational web tool for',
  'learning Linear Temporal Logic (LTL) model checking, developed as part of a COS700',
  'research project at the University of Pretoria (supervisor: Dr. Nils Timm).',
  '',
  'The session takes about 45 to 60 minutes in one sitting: a short knowledge test, a set',
  'of exercises completed in the tool, a second test of the same length, and a feedback',
  'survey.',
  '',
  'Participation is voluntary, has no effect on any academic marks, and you may stop at any',
  'time by closing this form.',
  '',
  'WHAT IS COLLECTED. This form does not collect your name, email address or student number.',
  'It records your answers to the two short tests and the feedback questions. The tool records',
  'your exercise attempts, meaning whether each submission was correct and how many hints you',
  'used. You sign in to the tool with any Google account you choose; like any website sign-in,',
  'the tool keeps that account so you can log in, but it is never exported or used for the',
  'research.',
  '',
  'HOW THE TWO ARE LINKED. On the next page you sign in to the tool and copy the participant code',
  'it shows you into this form. That code is the only link between your answers here and your',
  'attempts in the tool. The data used for the research contains the code, never your name or',
  'email.',
  '',
  'REPORTING. Results are reported as group totals and with participant numbers only. Nothing in',
  'the research report will name you or identify how you personally performed, including any',
  'comments that may be quoted.'
].join('\n');

var SIGN_IN_TEXT = [
  'Open the tool in a NEW BROWSER TAB, keeping this form open:',
  '',
  TOOL_URL,
  '',
  'Sign in with Google, using any Google account you like. Use the same account for the whole',
  'session: a different account shows a different code.',
  '',
  'Your PARTICIPANT CODE is shown at the top of the tool, next to Logout (on a phone, open',
  'Menu). It looks like P-3F9A2C. Copy it into the box below.',
  '',
  'Please ONLY copy the code for now. Do not open Exercises until this form tells you to. Leave',
  'the tool tab open and come back here.'
].join('\n');

var TOOL_PHASE_TEXT = [
  'Now go back to the tool tab. You should still be signed in with the same account; if not,',
  'sign in again with that account. The address is:',
  '',
  TOOL_URL,
  '',
  'Click EXERCISES in the top bar and complete the six exercises in order, Exercise 1 to',
  'Exercise 6. Use hints if you get stuck, and retry until each exercise is solved or you have',
  'spent a reasonable effort on it. This part takes roughly 30 minutes.',
  '',
  'When you are done, come back to this tab and continue.'
].join('\n');

// Each test item: [title, [choices...], indexOfCorrectChoice]
var FORM_A = [
  ['Let a path be the sequence of states s0, s1, s2 and so on. The formula X p holds on this path exactly when:',
   ['p holds in s1, the state directly after the first one',
    'p holds in s0, the first state',
    'p holds in some later state, no matter which',
    'p holds in every state from s1 onwards'], 0],
  ['Which formula says that p holds at some point, either now or later?',
   ['F p', 'G p', 'X p', 'p U p'], 0],
  ['Which reading of G F p is correct?',
   ['p keeps happening again and again, without end',
    'after some point, p holds forever',
    'p holds in every single state',
    'p holds once and then never again'], 0],
  ['For p U q to hold on a path, what is required?',
   ['q holds at some position, and p holds at every position before it',
    'p holds forever, whether or not q ever holds',
    'p and q hold together in the same state',
    'q holds in the state immediately after the first one'], 0],
  ['"The two processes are never in their critical sections at the same time" is a safety property. A path violates a safety property exactly when:',
   ['some finite portion of the path, read from the start, already shows the violation',
    'the violation can only be seen by inspecting the whole infinite path',
    'the path visits some state infinitely often',
    'safety properties cannot be violated'], 0],
  ['A drinks machine has states pay, select, soda and beer. From pay it moves to select; from select it moves to either soda or beer; from soda and from beer it returns to pay. Consider the path that starts at pay and then repeats the cycle (select, soda, pay) forever. Which formula does this path VIOLATE?',
   ['G F beer', 'F soda', 'G F soda', 'G !(soda and beer)'], 0]
];

var FORM_B = [
  ['Let a path be the sequence of states s0, s1, s2 and so on. The formula X q holds on this path exactly when:',
   ['q holds in s1, the state directly after the first one',
    'q holds in s0, the first state',
    'q holds at some later position, no matter which',
    'q holds in all states after s0'], 0],
  ['Which formula says that r holds in every state of the path?',
   ['G r', 'F r', 'X r', 'r U r'], 0],
  ['Which reading of F G r is correct?',
   ['after some point, r holds forever',
    'r keeps happening again and again, without end',
    'r holds in every single state',
    'r holds once and then never again'], 0],
  ['For r U s to hold on a path, what is required?',
   ['s holds at some position, and r holds at every position before it',
    'r holds forever, whether or not s ever holds',
    'r and s hold together in the same state',
    's holds in the state immediately after the first one'], 0],
  ['"Whenever a process asks to enter its critical section, it eventually gets in" is a liveness property. A path violates a liveness property:',
   ['only as an infinite path, since no finite portion of it can settle the question',
    'as soon as a finite portion of the path shows the violation',
    'whenever the process is in its critical section at the start',
    'liveness properties cannot be violated'], 0],
  ['Two processes are each outside their critical section (n), trying to enter (t), or inside it (c). In the structure, s0 is labelled n1 n2, s1 is t1 n2, s3 is t1 t2, and s7 is t1 c2. Consider the path that starts at s0 and then repeats the cycle (s1, s3, s7) forever. Which formula does this path VIOLATE?',
   ['G (t1 implies F c1)', 'G !(c1 and c2)', 'F t1', 'G F t1'], 0]
];

var LIKERT = [
  'I could figure out how to use the tool on my own, without a tutorial or outside help.',
  'Given the choice, I would rather practise model checking in this tool than on paper.',
  'After this session I feel more sure of myself when reading and writing LTL formulas.',
  'Working through the exercises held my attention.',
  'Getting an immediate result after each submission encouraged me to keep trying.',
  'I would tell other students taking Formal Methods to use this tool.',
  'When my answer was wrong, the tool\'s response helped me understand what to fix.',
  'The counterexample paths highlighted on the state graph made it clearer to me why a formula fails.'
];

var OPEN_ENDED = [
  'Which part of the tool did the most for your understanding, and why?',
  'If you could change one thing about the tool before it is used in a classroom, what would it be?',
  'Is there anything else you would like to tell us about your experience?'
];

// ---------------------------------------------------------------- builder

function createBothForms() {
  var g1 = buildForm('AB');
  var g2 = buildForm('BA');
  var out = [
    '',
    '=== LTLab evaluation forms created ===',
    '',
    'GROUP 1 (order AB: test A before the tool, test B after)',
    '  edit:  ' + g1.getEditUrl(),
    '  share: ' + g1.getPublishedUrl(),
    '',
    'GROUP 2 (order BA: test B before the tool, test A after)',
    '  edit:  ' + g2.getEditUrl(),
    '  share: ' + g2.getPublishedUrl(),
    '',
    'Send the SHARE links, following allocation_list.md row by row.',
    'Check the manual settings listed at the end of create_forms.gs before sending.',
    ''
  ].join('\n');
  Logger.log(out);
  return out;
}

/** order is 'AB' or 'BA'. */
function buildForm(order) {
  var preItems  = (order === 'AB') ? FORM_A : FORM_B;
  var postItems = (order === 'AB') ? FORM_B : FORM_A;
  var groupName = (order === 'AB') ? 'Group 1' : 'Group 2';

  var form = FormApp.create('LTLab evaluation study (' + groupName + ')');
  form.setDescription(CONSENT_TEXT);
  form.setIsQuiz(true);
  setEmailCollection(form);
  form.setProgressBar(true);
  form.setAllowResponseEdits(false);
  // Never shuffle question order: it would scramble the two tests together.
  form.setShuffleQuestions(false);

  // --- Section 1, consent
  form.addCheckboxItem()
      .setTitle('Consent')
      .setChoiceValues(['I have read the information above and consent to participate.'])
      .setRequired(true);

  // --- Section 2, sign in and participant code: the only link between this response and the tool.
  // Straight after consent so consent always precedes sign-in, and the tool tab is ready for section 5.
  form.addPageBreakItem()
      .setTitle('Sign in to the tool and copy your code')
      .setHelpText(SIGN_IN_TEXT);
  form.addTextItem()
      .setTitle('Participant code')
      .setHelpText('It looks like P-3F9A2C.')
      .setRequired(true)
      .setValidation(FormApp.createTextValidation()
          .setHelpText('Enter the code exactly as shown in the tool, for example P-3F9A2C.')
          .requireTextMatchesPattern(PARTICIPANT_CODE_PATTERN)
          .build());

  // --- Section 3, background
  form.addPageBreakItem()
      .setTitle('About you')
      .setHelpText('Three short questions before the first test.');

  form.addMultipleChoiceItem()
      .setTitle('What is your current level of study?')
      .setChoiceValues(['Honours', 'Postgraduate (MSc/PhD)', 'Other'])
      .setRequired(true);

  form.addMultipleChoiceItem()
      .setTitle('When did you last actively work with LTL or model checking?')
      .setChoiceValues(['Only during the Formal Methods module, not since',
                        'Within the last year (coursework, research, or work)',
                        'I work with it regularly'])
      .setRequired(true);

  form.addMultipleChoiceItem()
      .setTitle('What kind of device are you using for this session?')
      .setChoiceValues(['Laptop or desktop', 'Tablet', 'Phone'])
      .setRequired(true);

  // --- Section 4, pre-test
  form.addPageBreakItem()
      .setTitle('Knowledge check, part 1')
      .setHelpText('Six multiple-choice questions. Please answer from what you know now, '
                 + 'without looking anything up, and do not open Exercises in the tool yet.');
  addTest(form, preItems);

  // --- Section 5, tool phase
  form.addPageBreakItem()
      .setTitle('Now use the tool')
      .setHelpText(TOOL_PHASE_TEXT);

  // --- Section 6, post-test
  form.addPageBreakItem()
      .setTitle('Knowledge check, part 2')
      .setHelpText('Six more multiple-choice questions, on the same topics as before. '
                 + 'Again, please answer without looking anything up.');
  addTest(form, postItems);

  // --- Section 7, questionnaire
  form.addPageBreakItem()
      .setTitle('Your experience of the tool')
      .setHelpText('1 means strongly disagree and 5 means strongly agree.');
  for (var i = 0; i < LIKERT.length; i++) {
    form.addScaleItem()
        .setTitle(LIKERT[i])
        .setBounds(1, 5)
        .setLabels('Strongly disagree', 'Strongly agree')
        .setRequired(true);
  }

  // --- Section 8, open feedback
  form.addPageBreakItem()
      .setTitle('Anything else')
      .setHelpText('These are optional, but they are the most useful part of the feedback.');
  for (var j = 0; j < OPEN_ENDED.length; j++) {
    form.addParagraphTextItem().setTitle(OPEN_ENDED[j]).setRequired(false);
  }

  return form;
}

/**
 * Adds six graded multiple-choice items, one point each.
 *
 * The option order is shuffled here, at build time. In the source data the
 * correct option is always listed first, so without this every answer would sit
 * in the same position and give itself away. Shuffling in the script does not
 * depend on any form setting being right, and the resulting order is fixed for
 * all respondents, which keeps every participant's form identical.
 */
function addTest(form, items) {
  for (var i = 0; i < items.length; i++) {
    var title = items[i][0];
    var options = items[i][1];
    var correctIndex = items[i][2];

    var order = shuffledIndices(options.length);
    var item = form.addMultipleChoiceItem();
    var choices = [];
    for (var c = 0; c < order.length; c++) {
      var k = order[c];
      choices.push(item.createChoice(options[k], k === correctIndex));
    }
    item.setTitle(title)
        .setChoices(choices)
        .setPoints(1)
        .setRequired(true);
  }
}

/** Fisher-Yates over [0..n-1]. */
function shuffledIndices(n) {
  var a = [];
  for (var i = 0; i < n; i++) { a.push(i); }
  for (var j = a.length - 1; j > 0; j--) {
    var k = Math.floor(Math.random() * (j + 1));
    var t = a[j]; a[j] = a[k]; a[k] = t;
  }
  return a;
}

/**
 * Turns email collection off: the study must not collect student data, and the
 * participant code links responses to the tool instead.
 *
 * setCollectEmail is the Apps Script call; the EmailCollectionType enum belongs to
 * the separate Forms REST API and does not exist in FormApp.
 */
function setEmailCollection(form) {
  form.setCollectEmail(false);
}

/*
 * MANUAL SETTINGS TO CHECK ONCE, IN EACH FORM'S EDITOR
 * ----------------------------------------------------
 * Apps Script cannot set these, so they have to be done by hand on both forms:
 *
 *   1. Settings, Quizzes, Release grade: "Later, after manual review".
 *      Participants must not be shown their score or the answers.
 *   2. Settings, Quizzes, "Respondent can see": turn OFF missed questions,
 *      correct answers and point values.
 *   3. Settings, Responses: confirm "Collect email addresses" is "Do not collect"
 *      and "Limit to 1 response" is OFF (it would force a Google sign-in).
 *   4. Settings, Presentation: confirm "Shuffle question order" is OFF. It would
 *      mix the two tests together.
 *   5. Add the state-diagram screenshot to the final question of each test, the
 *      path-reading one, exported from the tool's sandbox.
 *   6. Submit one test response through each form yourself, then confirm the
 *      response sheet has NO email column, records the participant code and all
 *      twelve test answers, and delete that response before sending links out.
 *
 * NOTE ON OPTION ORDER
 * Apps Script has no per-question "shuffle options" setting; only form-level
 * question shuffling exists, which must stay off here. The script therefore
 * shuffles each question's options once, at build time, so the correct answer is
 * not always in the same position. The order is then identical for every
 * participant, which is what we want for a test.
 */
