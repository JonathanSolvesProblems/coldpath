# Office hours prep — Tue Jul 14, 10:00am PDT

**Where:** Arm Developer Program Discord — https://discord.com/invite/armsoftwaredev
**Second session:** Mon Aug 3, 10:00am PDT (keep it, for a progress check-in)
**Who'll be there:** the judges. Avin Zarlez, Michael Hall, Gabriel Peterson, Rani Mandepudi, Disha Patil.

## The one rule for this session

**Do not describe the project.** The forum and Discord are public and the field is ~1,227 people.
Every question below is designed to be genuinely useful to ask *and* to give away nothing about what
we're building. If someone asks what you're working on, "a developer tool for the Cloud AI track,
still nailing down the benchmark methodology" is the most you say.

## The two that actually de-risk us — ask these first

**1. Is a developer tool in scope for Cloud AI, or do you want an application?**

> "Looking at last year's Developer Challenge, every winner was an end-user application. This year is
> named an *Optimization* Challenge and the criteria list developer experience and reusable artifacts.
> If someone submits a developer tool or a piece of infrastructure rather than an app, does that
> still 'meet the spirit of the track'?"

This is the single highest-value question we can ask. Last year's six winners were *all* polished
consumer apps and none won on tooling. If the answer comes back "we want apps," we adjust the framing
to lead with a usable end-product rather than a library. Get this on the record.

**2. Do you prefer benchmarks we ran, or benchmarks you can re-run?**

> "For the 'exact benchmarks' guidance — is there a preference between reporting numbers we measured
> ourselves, versus wiring the benchmark into CI so a judge can re-run it and regenerate the table?
> And does Arm Performix output carry particular weight?"

Avin already said publicly *"we won't be judging just by a single metric, get nerdy in the details."*
This question invites her to expand on that, in public, in front of everyone — and the answer almost
certainly validates the reproducible-CI approach, which is free ammunition for the writeup.

## The cheap-visibility one — post this in the Discussions forum too

The forum is **completely empty**. Zero topics. Posting a well-posed question there is nearly free
visibility in front of all five judges before they ever open a submission.

**3. The rules page contradicts the track page.**

> "Small inconsistency in the rules: the Rules tab describes a Track 3 requiring only 'proof artifacts
> (links/screenshots)' and mentions 'scavenger deliverables' and 'scale + learning completion' — but the
> Track Details tab defines Track 3 as Mobile AI, and the global requirements say every submission needs
> a public MIT/Apache repo. Read literally, a Mobile AI entry wouldn't need source code. Which is right?
> Assuming source is required for all three tracks — just want to confirm before building."

This is real, it's helpful, it's the kind of thing a careful engineer notices, and it reads as
*"this person actually read the rules."* Michael Hall in particular has spent 15 years on
docs/onboarding and will register it.

## The rest, in priority order

**4. Cloud credits or hardware?**

> "Are there any cloud credits or dev boards for participants? Nothing's listed on the Devpost page,
> and the Cloud AI track's target hardware (Graviton, Cobalt, Axion) isn't free."

Worth two minutes. If credits exist, they're being handed out in Discord and we want them. If they
don't, we've confirmed everyone is equally constrained and our free-CI approach is a real edge.

**5. Are upstream contributions in scope?**

> "If part of a submission is a merged PR into an upstream open-source project (llama.cpp, ONNX Runtime,
> etc.) rather than code that lives only in our own repo, does that count toward the submission?"

Useful for the possible second act. Also, Avin and Rani *both* personally file upstream Arm64 fixes, so
this question is aimed straight at their values. Low risk of leaking anything.

**6. What did last year's winners get wrong?**

> "Three of you judged the previous challenge. What did strong-but-losing submissions consistently get
> wrong? Anything you saw over and over that you wish people had done differently?"

Open-ended, flattering, and the answer is usually the most actionable thing said all session.

## What to listen for (don't just talk)

- Anyone hinting at what they consider crowded or overdone. That's a free competitive read.
- Whether Performix use is expected vs. optional. It's Arm's flagship new tool and the brief name-drops it.
- Whether they care about mobile/Android at all in a Cloud AI submission (our S24 second act).
- Any mention of a Contribute-style parallel track. (GitLab Transcend had one and we missed it entirely.
  Ask if nobody brings it up.)

## After the session

- Write down verbatim quotes. Judge quotes are gold in the writeup and in the demo script.
- If any judge says something that contradicts `STRATEGY.md`, update the strategy, not the memory of it.
