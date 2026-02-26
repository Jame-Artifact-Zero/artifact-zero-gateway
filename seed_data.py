"""
Fortune 500 + VC Fund Seed Data
================================
Corporate text derived from actual public messaging patterns.
Each entry produces a different NTI score based on structural quality.
"""

FORTUNE_500 = [
    {
        "slug": "walmart",
        "name": "Walmart",
        "rank": 1,
        "url": "https://corporate.walmart.com",
        "text": "Walmart's purpose is to save people money so they can live better. We operate approximately 10,500 stores and clubs under 46 banners in 19 countries and eCommerce websites. We employ approximately 2.1 million associates worldwide. For the fiscal year ended January 31, 2025, Walmart's total revenue was $648 billion. Our strategy focuses on strengthening the core business while building new capabilities. We are committed to creating opportunities for our associates, strengthening local communities, and sustaining the planet. We believe everyone deserves access to affordable products and services. Our leadership team is dedicated to driving transformation across the enterprise through technology, supply chain innovation, and an unwavering focus on the customer experience. We will continue to invest in our people and our communities because that is what makes Walmart special."
    },
    {
        "slug": "amazon",
        "name": "Amazon",
        "rank": 2,
        "url": "https://www.aboutamazon.com",
        "text": "Amazon is guided by four principles: customer obsession rather than competitor focus, passion for invention, commitment to operational excellence, and long-term thinking. We strive to be Earth's most customer-centric company. Amazon employs over 1.5 million people worldwide and generated $637 billion in net sales in 2024. Our leadership principles guide every decision we make. We believe in being vocally self-critical, earning trust through transparency, and diving deep into problems. We hire and develop the best talent and insist on the highest standards. Our innovation spans cloud computing with AWS, entertainment with Prime Video and Studios, devices and services, and logistics infrastructure. We think big, act with urgency, and deliver results. Bias for action means speed matters in business and we value calculated risk-taking."
    },
    {
        "slug": "apple",
        "name": "Apple",
        "rank": 3,
        "url": "https://www.apple.com",
        "text": "Apple designs, manufactures, and markets smartphones, personal computers, tablets, wearables, and accessories worldwide. The company generated $391 billion in revenue in fiscal year 2024. Apple's mission is to bring the best user experience to customers through innovative hardware, software, and services. Our environmental commitments include being carbon neutral across our entire supply chain and product lifecycle by 2030. We currently use 100% recycled cobalt in all Apple-designed batteries. Privacy is a fundamental human right, and every Apple product is designed from the ground up to protect personal information. Accessibility features ensure our products work for everyone. We employ approximately 164,000 people."
    },
    {
        "slug": "unitedhealth",
        "name": "UnitedHealth Group",
        "rank": 4,
        "url": "https://www.unitedhealthgroup.com",
        "text": "UnitedHealth Group is a diversified health care company dedicated to helping people live healthier lives and helping make the health system work better for everyone. Our two distinct and complementary businesses — UnitedHealthcare and Optum — are working to help build a modern, high-performing health system through improved access, affordability, outcomes, and experiences. We serve approximately 152 million people globally, employ over 400,000 team members, and generated $371 billion in revenues in 2024. We believe everyone deserves the opportunity to live their healthiest life. Our values guide everything we do: integrity, compassion, relationships, innovation, and performance. We are committed to addressing the most pressing health care challenges of our time. Together, we are creating a health system that works better for absolutely everyone."
    },
    {
        "slug": "berkshire-hathaway",
        "name": "Berkshire Hathaway",
        "rank": 5,
        "url": "https://www.berkshirehathaway.com",
        "text": "Berkshire Hathaway is a holding company owning subsidiaries engaged in insurance and reinsurance, freight rail transportation, energy generation and distribution, manufacturing, retailing, and services. The company had revenues of $364 billion in 2024 and approximately 396,000 employees. Chairman Warren Buffett's annual letter to shareholders outlines the operating results of the year. Berkshire's acquisition criteria: demonstrated consistent earning power, businesses earning good returns on equity while employing little or no debt, management in place, simple businesses, and an offering price. We do not participate in auctions. We can promise complete confidentiality and a very fast answer as to whether we are interested, customarily within five minutes."
    },
    {
        "slug": "cvs-health",
        "name": "CVS Health",
        "rank": 6,
        "url": "https://www.cvshealth.com",
        "text": "CVS Health is the leading health solutions company, delivering care in ways no one else can. We reach approximately 185 million people annually through our unique combination of assets: over 9,000 retail pharmacy locations, a leading pharmacy benefits manager serving more than 110 million plan members, a leading health insurer with approximately 26 million medical members, and expanding health care delivery capabilities. Our purpose — bringing our heart to every moment of your health — guides our commitment to transforming health care and improving consumer health outcomes. We believe health care should be simpler, more accessible, and more affordable. We are uniquely positioned to meet the evolving needs of our customers, members, and patients. Our integrated model creates better health outcomes at a lower total cost of care. Together, we are creating a world of healthier communities."
    },
    {
        "slug": "exxonmobil",
        "name": "ExxonMobil",
        "rank": 7,
        "url": "https://corporate.exxonmobil.com",
        "text": "ExxonMobil is one of the world's largest publicly traded energy providers and chemical manufacturers. We develop and apply next-generation technologies to help safely and responsibly meet the world's growing needs for energy and high-quality chemical products. The company's 2024 earnings were $33.7 billion on revenues of $344 billion, with approximately 62,000 employees globally. Our approach to sustainability includes reducing emissions intensity across operations while investing in lower-emission technologies. We are advancing carbon capture and storage with a capacity target of 50 million metric tons per year by 2040. Our Permian Basin operations achieved record production levels. We maintain disciplined capital allocation with a focus on high-return investments."
    },
    {
        "slug": "alphabet",
        "name": "Alphabet (Google)",
        "rank": 8,
        "url": "https://about.google",
        "text": "Google's mission is to organize the world's information and make it universally accessible and useful. Alphabet, Google's parent company, generated $350 billion in revenues in 2024 and employs approximately 182,000 people. Our products serve billions of users worldwide including Search, YouTube, Android, Chrome, Google Cloud, and Pixel devices. Google AI is our most profound area of investment, with Gemini models advancing capabilities across every product. We are committed to developing AI responsibly with published AI Principles guiding our work since 2018. Sustainability efforts include operating on 24/7 carbon-free energy by 2030 across all data centers and offices. Google Cloud serves millions of organizations and generated $43 billion in revenue."
    },
    {
        "slug": "mckesson",
        "name": "McKesson",
        "rank": 9,
        "url": "https://www.mckesson.com",
        "text": "McKesson Corporation is a diversified healthcare services leader dedicated to advancing health outcomes for patients everywhere. As a global healthcare supply chain management solutions company, retail pharmacy, community oncology and specialty care, and healthcare information technology leader, we touch virtually every aspect of healthcare. Our fiscal year 2025 revenues exceeded $309 billion. We distribute approximately one-third of all pharmaceuticals used in North America. Our purpose is advancing health outcomes for all. We employ over 51,000 team members. McKesson's pharmaceutical distribution network spans 30 strategically located distribution centers in the U.S. and Canada."
    },
    {
        "slug": "amerisourcebergen",
        "name": "Cencora",
        "rank": 10,
        "url": "https://www.cencora.com",
        "text": "Cencora, formerly AmerisourceBergen, is a leading global pharmaceutical solutions organization centered on improving the lives of people and animals. We create unparalleled access and efficiency in pharmaceutical care. Our fiscal year 2024 revenue was $284 billion. Our purpose is to create healthier futures. We are united in our responsibility to build healthier futures for all stakeholders. Our solutions span pharmaceutical distribution, specialty logistics, consulting services, and animal health. We operate in more than 50 countries and partner with global manufacturers, pharmacies, health systems, and patients. We believe innovation in pharmaceutical care is absolutely essential to meeting the world's growing healthcare needs."
    },
    {
        "slug": "microsoft",
        "name": "Microsoft",
        "rank": 12,
        "url": "https://www.microsoft.com/en-us/about",
        "text": "Microsoft's mission is to empower every person and every organization on the planet to achieve more. In fiscal year 2024, the company reported revenues of $245 billion and employs approximately 228,000 people worldwide. Our three commercial segments are Productivity and Business Processes (Office, LinkedIn, Dynamics), Intelligent Cloud (Azure, server products, enterprise services), and More Personal Computing (Windows, devices, gaming, search). Azure AI services now serve over 60,000 organizations. Microsoft 365 has 400 million paid seats. LinkedIn reaches more than one billion members globally. Our responsible AI framework covers fairness, reliability, privacy, inclusiveness, transparency, and accountability. We have committed to becoming carbon negative by 2030 and to removing all historical carbon emissions by 2050."
    },
    {
        "slug": "jpmorgan-chase",
        "name": "JPMorgan Chase",
        "rank": 17,
        "url": "https://www.jpmorganchase.com",
        "text": "JPMorgan Chase is a leading financial services firm with assets of $4.0 trillion and operations worldwide. The firm is a leader in investment banking, financial services for consumers and small businesses, commercial banking, financial transaction processing, and asset management. We employ approximately 309,000 people. Our firm generated net revenue of $162 billion in 2024. We serve millions of consumers, businesses, and institutional clients through our four business segments. Our commitment to corporate responsibility is embedded in how we do business. We invested $30 billion in racial equity and affordable housing. JPMorgan Chase operates in more than 100 countries."
    },
    {
        "slug": "ford",
        "name": "Ford Motor",
        "rank": 24,
        "url": "https://corporate.ford.com",
        "text": "Ford Motor Company is a global company based in Dearborn, Michigan, committed to helping build a better world where every person is free to move and pursue their dreams. The company designs, manufactures, markets, and services a full line of Ford trucks, utility vehicles, and cars — increasingly including electrified versions — and Lincoln luxury vehicles. Ford generated $185 billion in revenue in 2024 and employs approximately 177,000 people. Ford Pro delivers a comprehensive suite of software and services for commercial customers. Ford Model e is scaling electric vehicle production. Ford Blue strengthens the iconic lineup of gas and hybrid vehicles. Our strategy is built on strength, which means playing to win in our areas of strength rather than trying to be all things to all people."
    },
    {
        "slug": "meta",
        "name": "Meta Platforms",
        "rank": 29,
        "url": "https://about.meta.com",
        "text": "Meta builds technologies that help people connect, find communities, and grow businesses. Our family of apps — Facebook, Instagram, WhatsApp, and Messenger — is used by billions of people around the world. We are also developing augmented and virtual reality technologies through Reality Labs, including Meta Quest headsets and Ray-Ban Meta smart glasses. Meta reported $164 billion in revenues in 2024 and employs approximately 72,000 people. We are investing heavily in artificial intelligence, including our open-source Llama models which have been downloaded over 700 million times. Our responsible innovation approach ensures we develop AI that is safe and beneficial. We believe the metaverse will be the successor to the mobile internet and we are investing accordingly."
    },
    {
        "slug": "tesla",
        "name": "Tesla",
        "rank": 42,
        "url": "https://www.tesla.com",
        "text": "Tesla's mission is to accelerate the world's transition to sustainable energy. We design, develop, manufacture, and sell electric vehicles, energy generation systems, and energy storage products. Tesla delivered 1.79 million vehicles in 2024 and generated $97 billion in revenue. Our product lineup includes Model S, Model 3, Model Y, Model X, Cybertruck, and the Tesla Semi. Tesla Energy deploys Megapack battery storage systems and Solar Roof. Our Supercharger network includes over 60,000 connectors globally. Autopilot and Full Self-Driving capability represent our vision for autonomous transportation. We operate Gigafactories in Fremont, Austin, Shanghai, Berlin, and soon in Mexico. Tesla employs approximately 140,000 people. We do not pay for advertising."
    },
    {
        "slug": "boeing",
        "name": "Boeing",
        "rank": 50,
        "url": "https://www.boeing.com",
        "text": "Boeing is the world's largest aerospace company and a leading manufacturer of commercial jetliners, defense, space, and security systems. The company supports airlines and government customers in more than 150 countries. Boeing reported $66 billion in revenues in 2024 and employs approximately 171,000 people. We are absolutely committed to safety and quality as foundational values. The company is executing on a comprehensive plan to stabilize production, improve quality systems, and strengthen our culture of safety. We are investing in our workforce and our production systems to meet the growing demand for commercial aircraft. Our defense and space portfolio includes advanced fighters, rotorcraft, satellites, and autonomous systems. We believe aerospace will continue to bring the world together."
    },
    {
        "slug": "disney",
        "name": "Walt Disney",
        "rank": 44,
        "url": "https://thewaltdisneycompany.com",
        "text": "The Walt Disney Company, together with its subsidiaries, is a diversified worldwide entertainment company with operations in Disney Entertainment, ESPN, and Disney Experiences. The company generated $91 billion in revenue in 2024 and employs approximately 225,000 cast members worldwide. Our mission is to entertain, inform, and inspire people around the globe through the power of unparalleled storytelling. Disney+ has surpassed 150 million subscribers globally. Our theme parks and cruise lines create magical experiences for millions of guests annually. ESPN is transforming into a direct-to-consumer flagship platform. We believe in the power of creativity and innovation to bring joy to people of all ages. Our stories have the incredible ability to connect across cultures and generations."
    },
    {
        "slug": "pfizer",
        "name": "Pfizer",
        "rank": 36,
        "url": "https://www.pfizer.com",
        "text": "Pfizer's purpose is breakthroughs that change patients' lives. We discover, develop, manufacture, and market medicines, vaccines, and consumer healthcare products. In 2024, Pfizer generated $58 billion in revenues and invested $10.5 billion in research and development. We currently have approximately 113 programs in clinical development across oncology, immunology, rare diseases, and anti-infectives. Our manufacturing network includes 42 sites in 14 countries. Pfizer employs approximately 88,000 colleagues worldwide. We delivered over 4 billion COVID-19 vaccine doses globally. Our oncology portfolio includes 24 approved medicines and represents the fastest-growing segment of our business. We are committed to ensuring our breakthroughs reach everyone who can benefit, regardless of where they live."
    },
    {
        "slug": "costco",
        "name": "Costco",
        "rank": 11,
        "url": "https://www.costco.com/about.html",
        "text": "Costco Wholesale operates an international chain of membership warehouses that carry quality, brand-name merchandise at substantially lower prices than are typically found at conventional wholesale or retail sources. Costco had total revenues of $254 billion in fiscal 2024 and operates 891 warehouses worldwide. We employ approximately 316,000 people. The company's strategy is straightforward: keep costs down and pass the savings on to our members. We limit our markup to 14% on branded goods and 15% on Kirkland Signature products. Membership fees totaled $4.8 billion. Our employee compensation exceeds industry averages, with starting hourly pay above $17 and an average hourly wage of $30. We believe treating employees well reduces turnover and drives productivity."
    },
    {
        "slug": "ibm",
        "name": "IBM",
        "rank": 48,
        "url": "https://www.ibm.com",
        "text": "IBM is a leading provider of global hybrid cloud and AI technology and consulting expertise. The company's revenue was $62.8 billion in 2024 with approximately 288,000 employees in more than 175 countries. Our strategy is focused on hybrid cloud platform and AI. IBM watsonx is our enterprise AI and data platform that helps organizations scale and accelerate the impact of AI. Red Hat provides the leading enterprise open-source software. IBM Consulting helps businesses modernize and transform. We have been granted over 150,000 patents — more than any other company. Our commitment to responsible AI includes governance tools that help organizations deploy AI they can trust. We invested $6.8 billion in R&D in 2024."
    },
]

VC_FUNDS = [
    {
        "slug": "sequoia",
        "name": "Sequoia Capital",
        "rank": 1,
        "url": "https://www.sequoiacap.com",
        "text": "Sequoia Capital helps daring founders build legendary companies from idea to IPO and beyond. We partner with founders at every stage — from the spark of an idea through the growth into an enduring company. Our portfolio includes Apple, Google, Oracle, YouTube, Instagram, WhatsApp, Stripe, and many more. We have helped build companies worth over $3.3 trillion in combined stock market value. Sequoia operates across the United States, China, India, Southeast Asia, and Europe. We take a long-term view and are willing to be patient. Our funds span seed, venture, growth, and public stages."
    },
    {
        "slug": "a16z",
        "name": "Andreessen Horowitz",
        "rank": 2,
        "url": "https://a16z.com",
        "text": "Andreessen Horowitz (a16z) is a venture capital firm that backs bold entrepreneurs building the future through technology. We manage approximately $42 billion in assets across multiple funds including bio and health, crypto, games, infrastructure, and enterprise. Our unique model provides portfolio companies with access to an extensive network of executives, engineers, and domain experts. We believe software is eating the world and that AI represents the most transformative technology since the internet. Our American Dynamism practice invests in companies that support the national interest. We are big believers in founder-led companies and believe the best founders are unreasonable people with extraordinary conviction. It's time to build."
    },
    {
        "slug": "general-catalyst",
        "name": "General Catalyst",
        "rank": 4,
        "url": "https://www.generalcatalyst.com",
        "text": "General Catalyst is a venture capital firm that invests in powerful, positive change that endures. We partner with founders from seed to growth and beyond to build companies that withstand the test of time. Our portfolio includes Stripe, Airbnb, Snap, Kayak, and Deliveroo. We manage over $25 billion in capital. Our approach to responsible innovation means we don't just invest in technology — we invest in the human systems around it. We believe that building enduring companies requires a fundamentally different approach. We are rethinking venture capital itself, transforming from a fund model to an enduring company. Health Assurance is our transformational approach to healthcare that aims to keep people healthy rather than treating them when they are sick."
    },
    {
        "slug": "benchmark",
        "name": "Benchmark",
        "rank": 5,
        "url": "https://www.benchmark.com",
        "text": "Benchmark is an early-stage venture capital firm. We invest in the earliest stages of technology companies. Each partner is an equal, with equal economics. We maintain a single $425 million fund and invest exclusively at the seed and Series A stage. We have backed eBay, Twitter, Uber, Snap, Discord, and many other companies. We do not have associates, analysts, or operating partners. Every portfolio company works directly with a partner. We believe this model produces better outcomes for founders and for us."
    },
    {
        "slug": "founders-fund",
        "name": "Founders Fund",
        "rank": 9,
        "url": "https://foundersfund.com",
        "text": "Founders Fund invests in smart people solving difficult problems, often ones that others think are impossible. We wanted flying cars, instead we got 140 characters. This observation drives our investment thesis: we look for companies building transformative technology, not incremental improvements. Our investments include SpaceX, Palantir, Anduril, Stripe, and Airbnb. We manage approximately $12 billion. We invest across stages from seed to growth. We believe the best founders have strong, often contrarian, views about the future. The next important company might not look like any existing company. We do not invest in companies that are slightly better versions of something that already exists."
    },
    {
        "slug": "kleiner-perkins",
        "name": "Kleiner Perkins",
        "rank": 6,
        "url": "https://www.kleinerperkins.com",
        "text": "Kleiner Perkins has been at the forefront of venture capital since 1972. We partner with the brightest entrepreneurs to turn disruptive ideas into world-changing companies. Our history includes Amazon, Google, Genentech, Twitter, and Figma. We invest at the seed, early, and growth stages across consumer technology, enterprise software, fintech, hardtech, and healthcare. Our team brings decades of operational experience in addition to capital. We believe the best venture capitalists are those who have built things themselves. We help founders navigate the hardest challenges in company building: hiring, strategy, and scaling."
    },
    {
        "slug": "tiger-global",
        "name": "Tiger Global Management",
        "rank": 11,
        "url": "https://www.tigerglobal.com",
        "text": "Tiger Global Management is a New York-based investment firm focused on public and private companies in the global internet, software, and financial technology sectors. The firm manages approximately $58 billion. Our private equity portfolio has included investments in over 400 companies across more than 30 countries. We combine fundamental research with quantitative analysis. Our technology investment team evaluates thousands of companies annually. We have invested in companies including JD.com, Facebook, LinkedIn, Spotify, and Stripe."
    },
    {
        "slug": "lux-capital",
        "name": "Lux Capital",
        "rank": 27,
        "url": "https://www.luxcapital.com",
        "text": "Lux Capital invests in emerging science and technology ventures at the outermost edges of what is possible. We back founders who are building at the intersection of technology and physical sciences. Our portfolio includes companies in synthetic biology, robotics, autonomous systems, space, advanced manufacturing, and quantum computing. We believe the most impactful companies of the next century will be built by scientists and engineers solving problems that were previously considered unsolvable. Lux manages over $5 billion across multiple funds. Our investments include Anduril, Desktop Metal, Echodyne, and Kallyope."
    },
    {
        "slug": "usv",
        "name": "Union Square Ventures",
        "rank": 17,
        "url": "https://www.usv.com",
        "text": "Union Square Ventures is a thesis-driven venture capital firm. We invest in trusted brands that broaden access to knowledge, capital, and well-being by leveraging networks, platforms, and protocols. Our portfolio includes Twitter, Tumblr, Etsy, Coinbase, Cloudflare, and MongoDB. We manage funds totaling approximately $2 billion. We publish our investment thesis openly and blog regularly about what we learn. We believe transparency makes us better investors and helps founders decide if we are the right partner."
    },
    {
        "slug": "500-global",
        "name": "500 Global",
        "rank": 16,
        "url": "https://500.co",
        "text": "500 Global is a venture capital firm with more than $2.8 billion in assets under management that invests in founders building fast-growing technology companies. We have invested in over 2,700 companies across 81 countries. Our accelerator programs are designed to help founders everywhere build and scale companies. We believe the best founders can come from anywhere and shouldn't have to be in Silicon Valley to succeed. Our global network provides portfolio companies with access to customers, partners, and talent in markets around the world. We are committed to building a more inclusive venture ecosystem. We offer programs specifically designed for underrepresented founders."
    },
    {
        "slug": "fifth-wall",
        "name": "Fifth Wall",
        "rank": 30,
        "url": "https://fifthwall.com",
        "text": "Fifth Wall is the largest venture capital firm focused on technology solutions for the global real estate industry. We manage over $3.5 billion and our strategic limited partners include over 100 of the world's largest owners and operators of real estate. Our investment areas include climate technology, proptech, construction technology, and real estate fintech. We believe the built world is the largest asset class on the planet and the least technologically advanced. Our Climate Fund invests in technologies that will decarbonize the built environment. We connect portfolio companies directly with our LP network for pilot programs and commercial contracts."
    },
]
