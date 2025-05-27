import nodemailer from "nodemailer";

export default async function handler(req, res) {
  if (req.method !== "POST") {
    return res.status(405).json({ message: "Method Not Allowed" });
  }

  const { name, email, topic, message, spread } = req.body;

  const transporter = nodemailer.createTransport({
    service: "Gmail",
    auth: {
      user: process.env.GMAIL_USER,
      pass: process.env.GMAIL_PASS
    }
  });

  try {
    await transporter.sendMail({
      from: `"타로존 리딩 요청" <${process.env.GMAIL_USER}>`,
      to: process.env.GMAIL_USER,
      subject: `[타로 리딩 요청] ${topic} - ${name}`,
      text: `
📮 실물 리딩 요청이 도착했어요!

🧑 이름/닉네임: ${name}
📧 회신 이메일: ${email}
🔖 주제: ${topic}
🎴 카드 배열: ${spread || "선택 안함"}

💬 질문 내용:
${message}
      `
    });

    // ✅ 메일 전송 완료 후 thankyou.html로 이동
    res.redirect(302, "/thankyou.html");

  } catch (error) {
    console.error("메일 전송 실패", error);
    res.status(500).json({ message: "메일 전송 실패", error });
  }
}
