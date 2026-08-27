import { CtaSection } from "./_components/CtaSection";
import { Footer } from "./_components/Footer";
import { Hero } from "./_components/Hero";
import { HowItWorksSection } from "./_components/HowItWorksSection";
import { Navbar } from "./_components/Navbar";
import { ProblemSection } from "./_components/ProblemSection";
import { ProductPreview } from "./_components/ProductPreview";
import { WhySection } from "./_components/WhySection";

export default function Home() {
  return (
    <>
      <Navbar />
      <main>
        <Hero />
        <ProductPreview />
        <ProblemSection />
        <HowItWorksSection />
        <WhySection />
        <CtaSection />
      </main>
      <Footer />
    </>
  );
}
