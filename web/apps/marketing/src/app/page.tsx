import { CtaSection } from "./_components/CtaSection";
import { Footer } from "./_components/Footer";
import { Hero } from "./_components/Hero";
import { Navbar } from "./_components/Navbar";
import { PricingSection } from "./_components/PricingSection";
import { ProductPreview } from "./_components/product-preview";
import { WhySection } from "./_components/WhySection";
import { WorkflowSection } from "./_components/WorkflowSection";

export default function Home() {
  return (
    <>
      <Navbar />
      <main>
        <Hero />
        <ProductPreview />
        <WorkflowSection />
        <WhySection />
        <PricingSection />
        <CtaSection />
      </main>
      <Footer />
    </>
  );
}
