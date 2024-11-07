"""
TODO: OBSOLETE move/use from LIBS !
"""

from naeural_core.local_libraries.llm_api.openai.app import OpenAIApp

PERSONAS_FOLDER = './plugins/utils/openai/personas'
  
if __name__ == '__main__':
  DATA_TEST = False
  CODEGEN = True

  if DATA_TEST:
    query = 'Please generate in json format quizes where the answer is single word and completes the question. Propose another 4 wrong asnwers. Thematic is elementary school math. The questions should be amusing. Simplify the json format such as the "question" is "q", "correct_answer" is "a", "wrong_answers" is "wa" and the language "l":"en" or "l":"ro". Write the json puting each object in one line with the keys in the following order: "l", "q", "a", "wa" and end the line with a comma. Generate a total of 50 quizes for Romanian language'
    eng = OpenAIApp(persona="Worker", user='Andrei')
    res = eng.ask_direct(query)
    print(res)
    
  if CODEGEN:
    eng1 = OpenAIApp(persona='codegen', user='a1', persona_location=PERSONAS_FOLDER)
    r11 = eng1.ask("Write a simple hello world program in c++")
    eng2 = OpenAIApp(persona='codegen', user='a1', persona_location=PERSONAS_FOLDER)    
    r21 = eng2.ask("Write a simple hello world program in python")

    r12 = eng1.ask("Modify the program so that it prints the current date and time")
    r22 = eng2.ask("Modify the program so that it prints the system memory usage")
    
    print(r11)
    print("***********")
    print(r12)
    print("######################################################")
    print("######################################################")
    print(r21)
    print("***********")
    print(r22)
