from flask import Flask, render_template, request, redirect, url_for, session
import json
import random

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# Load questions from JSON file
def load_questions():
    with open('questions.json') as f:
        return json.load(f)["SAPBasisQuestions"]

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start_test')
def start_test():
    questions = load_questions()
    indices = random.sample(range(len(questions)), 5)
    session['questions'] = [questions[i] for i in indices]
    session['current_question'] = 0
    session['score'] = 0
    session['answers'] = []
    return redirect(url_for('test'))

@app.route('/test')
def test():
    current_question = session.get('current_question', 0)
    questions = session.get('questions', [])
    if current_question < len(questions):
        question = questions[current_question]
        return render_template('test.html', question=question, current_question=current_question+1)
    else:
        return redirect(url_for('result'))

@app.route('/submit', methods=['POST'])
def submit():
    current_question = session.get('current_question', 0)
    questions = session.get('questions', [])
    if current_question < len(questions):
        selected_option = request.form.get('option')
        correct_option = None
        for key, value in questions[current_question]['options'].items():
            if value == questions[current_question]['answer']:
                correct_option = key
                break
        if selected_option == correct_option:
            session['score'] += 1
            session['answers'].append({'question': questions[current_question], 'correct': True, 'selected_option': selected_option})
        else:
            session['answers'].append({'question': questions[current_question], 'correct': False, 'selected_option': selected_option})
        session['current_question'] += 1
    return redirect(url_for('test'))

@app.route('/result')
def result():
    score = session.get('score', 0)
    answers = session.get('answers', [])
    incorrect_answers = [answer for answer in answers if not answer['correct']]
    return render_template('result.html', score=score, incorrect_answers=incorrect_answers)

if __name__ == '__main__':
    app.run(debug=True)

