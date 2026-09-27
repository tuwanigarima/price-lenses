import re

with open("app.py", "r") as f:
    content = f.read()

# 1. Update the stream loop messages
content = content.replace(
    """                    elif node_name == "decision_synthesizer":
                        st.write("ℹ️ Decision synthesizer is pending future implementation")
                    elif node_name == "verifier_gate":
                        st.write("ℹ️ Final verifier is pending future implementation")""",
    """                    elif node_name == "decision_synthesizer":
                        st.write("✅ Decision synthesizer completed")
                    elif node_name == "verifier_gate":
                        st.write("✅ Final verifier completed")"""
)

# 2. Update the subtitle
content = content.replace(
    '        "parallel. The final cross-agent synthesizer is not implemented yet."\n    )',
    '        "parallel, followed by a decision synthesizer and grounding verifier."\n    )'
)

# 3. Replace the placeholder info box with the actual verdict UI
verdict_ui = """    
    final = result.get("final_verdict")
    draft = result.get("draft_verdict")
    
    if final and final.get("decision"):
        decision = final.get("decision")
        color = "green" if decision == "BUY_NOW" else "orange" if decision == "WAIT" else "red"
        
        st.markdown("---")
        st.subheader("🎯 Final Synthesized Verdict")
        
        col1, col2 = st.columns([1, 2])
        with col1:
            st.markdown(f"<h2 style='text-align: center; color: {color};'>{decision.replace('_', ' ')}</h2>", unsafe_allow_html=True)
            st.metric("Confidence", f"{final.get('confidence_score', 0):.0%}")
        
        with col2:
            st.markdown(f"**Target/Recommended Price:** ₹{final.get('target_price', 0):,.0f}")
            retailer = final.get('recommended_retailer') or 'N/A'
            st.markdown(f"**Recommended Retailer:** {retailer}")
            st.markdown(f"**Rationale:** {final.get('primary_rationale', '')}")
            
        if draft and draft != final:
            st.warning("⚠️ The deterministic verifier gate modified the LLM's draft verdict to enforce grounding rules.")
        st.markdown("---")
    else:
        st.warning("Synthesizer ran, but returned no final verdict.")
"""

content = content.replace(
    """    st.info(
        "Synthesizer: pending future implementation. No combined BUY/WAIT verdict "
        "has been generated."
    )""",
    verdict_ui
)

with open("app.py", "w") as f:
    f.write(content)
