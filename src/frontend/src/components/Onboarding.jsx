import { useEffect, useState } from 'react'
import homeImage from '../assets/onboarding/1-home.png'
import thesisImage from '../assets/onboarding/2-thesis.png'
import podcastsImage from '../assets/onboarding/3-podcasts.png'
import emailImage from '../assets/onboarding/4-email.png'
import researchImage from '../assets/onboarding/5-research.png'
import graphImage from '../assets/onboarding/6-graph.png'
import WalkthroughNav from './WalkthroughNav.jsx'
import './Onboarding.css'

const STEPS = [
  {
    title: 'Welcome to your Podcast Analyst',
    body: <>Your team’s virtual coworker for the podcasts that matter to your theses. It listens to new
      episodes as they’re released, extracts who claimed what (with the exact quote), and emails you when
      the evidence turns against you.</>,
    image: homeImage,
    alt: 'The Podcast Analyst home screen: an empty chat with suggested prompts, and the left-hand rail.',
  },
  {
    title: 'Start with what you believe',
    body: <>Tell the agent your investment thesis in plain English. It’ll help you sharpen it, save it, and
      check it right away against every claim it already has.</>,
    tryText: 'New thesis: Microsoft captures enterprise AI spending through both Azure and its own apps.',
    image: thesisImage,
    alt: 'The agent confirming a newly saved Microsoft thesis, with the Theses drawer open.',
  },
  {
    title: 'Point it at your podcasts',
    body: <>Ask it to follow a show: it finds the RSS feed and ingests new episodes as they come out.
      Already have an episode? Click <strong>Upload podcast</strong> and drop in a transcript or MP3.</>,
    tryText: 'Follow the a16z podcast.',
    image: podcastsImage,
    alt: 'The Podcasts drawer open, listing the podcast feeds being watched for new episodes.',
  },
  {
    title: 'A devil’s advocate in your inbox',
    body: <>Ask for email alerts on a thesis. When a new episode makes a credible, high-signal case against
      it, you’ll get the claims, who said them, and the exact quotes, plus a link to dig in with the
      agent.</>,
    tryText: 'Email me at you@fund.com if anything challenges my optical-interconnect thesis.',
    image: emailImage,
    alt: 'An alert email listing claims that challenge an optical-interconnect thesis, each with its speaker, exact quote, and episode.',
  },
  {
    title: 'Ask anything, get the receipts',
    body: <>Ask what’s been said about a company, a trend, or your thesis. Answers cite the speaker,
      episode, and verbatim quote, starting with the highest-signal claims.</>,
    tryText: 'What are the strongest claims against my Nvidia thesis?',
    footnote: 'Signal (0–100) = how specific, material, and well-sourced a claim is.',
    image: researchImage,
    alt: 'The research chat with a cited answer listing claims, speakers, and quotes.',
  },
  {
    title: 'See how it all connects',
    body: <>Your theses, claims, companies, and speakers live in one knowledge graph. Ask how two theses
      relate and watch the agent traverse it in real time, or open <strong>Show knowledge graph</strong> to
      explore on your own.</>,
    tryText: 'Show me in the knowledge graph how my Nvidia and optical-interconnect theses connect.',
    image: graphImage,
    alt: 'A graph query in chat and the knowledge graph card it produced, linking the Nvidia thesis to supporting and challenging claims and their episodes.',
  },
]

// A six-page primer: one screenshot and a few lines per page, Previous/Next, then into the app.
export default function Onboarding() {
  const [index, setIndex] = useState(0)
  const step = STEPS[index]

  // Load every screenshot up front so Next never shows an empty frame.
  useEffect(() => { for (const { image } of STEPS) new Image().src = image }, [])

  return (
    <main className="onboarding">
      <div className="onboarding-page">
        <div className="onboarding-shot"><img src={step.image} alt={step.alt} /></div>
        <div className="onboarding-text">
          <h1>{step.title}</h1>
          <p>{step.body}</p>
          {step.tryText && (
            <p className="onboarding-try"><span>Try saying</span><span className="onboarding-quote">“{step.tryText}”</span></p>
          )}
          {step.footnote && <p className="onboarding-footnote">{step.footnote}</p>}
        </div>
        <WalkthroughNav index={index} count={STEPS.length}
          onPrevious={() => setIndex(index - 1)} onNext={() => setIndex(index + 1)}
          finishHref="/app/" finishLabel="Start researching →" />
      </div>
    </main>
  )
}
