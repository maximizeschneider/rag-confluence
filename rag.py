import os 
from dotenv import load_dotenv
from langchain_openai.chat_models import ChatOpenAI 
from langchain_core.output_parsers import StrOutputParser
from langchain.prompts import ChatPromptTemplate 
from langchain_core.prompts import MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from langsmith import Client
from langchain.callbacks import LangChainTracer
from langchain_openai.embeddings import OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from langchain.vectorstores.chroma import Chroma
from dotenv import load_dotenv
from langchain_core.runnables import RunnableParallel, RunnableLambda, RunnablePassthrough
from operator import itemgetter
from typing import Literal, Generator 
        
# load .env Variablen 
load_dotenv()

# tracing with Langsmith from Langchain
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_PROJECT"] = "RAG-project"
os.environ["LANGCHAIN_ENDPOINT"] = "https://api.smith.langchain.com"
os.environ["LANGCHAIN_API_KEY"] = os.getenv("LANGCHAIN_API_KEY")
client = Client()

def initialize_chain():
    # template to control for hallucination and contradictions
    rag_template = """
        FRAGE: {input} 
        
        KONTEXT: {context}
        
        ANWEISUNG: 
        Beantworte die FRAGE basierend auf dem gegebenen KONTEXT.  
        Wenn KONTEXT leer ist, anworte mit "Ich weiß es nicht".
        Wenn das Beantworten der FRAGE nicht möglich ist durch den gegebenen KONTEXT, antworte immer "Ich weiß es nicht".
        
        """

    rag_prompt = ChatPromptTemplate.from_messages([
        ("system", rag_template),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{input}"),
        ]) 
    
    # rewrite query given the chat_history
    rewriting_template = """
        FRAGE: {input}
        
        KONTEXT: {chat_history}
        
        ANWEISUNG: 
        Ergänze FRAGE mit Informationen aus KONTEXT, wenn diese relevant zur FRAGE sind. Gib nur die transformierte FRAGE aus.
        """

    rewriting_prompt = ChatPromptTemplate.from_messages([MessagesPlaceholder(variable_name = "chat_history"), 
                                                    ("user", rewriting_template)
                                                    ])       

    # inistialize specific model with api key and temperature    
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    model = ChatOpenAI(openai_api_key=OPENAI_API_KEY, model="gpt-3.5-turbo", temperature=0)

    # parses AIMessage to string
    parser = StrOutputParser() 

    # for embedding of chunks 
    embeddings = OpenAIEmbeddings()

    # initialize vectorstore
    index_pinecone = "llm-km"
    vectorstore = PineconeVectorStore(index_name=index_pinecone, embedding=embeddings)

    # hyperparameters for retriever 
    MAX_NUMBER_OF_RESULTS = 10
    THRESHHOLD_SIMILARITY = 0.9

    # specifiy vectorstore and parameters for retriever 
    retriever = vectorstore.as_retriever(search_type="similarity_score_threshold", search_kwargs={"k": MAX_NUMBER_OF_RESULTS, "score_threshold": THRESHHOLD_SIMILARITY})

    # 2 functions for chaining:
    # compose page content of Documents to one string
    def format_docs(docs):
        global relevant_docs
        relevant_docs = docs
        return "\n\n".join([d.page_content for d in docs])

    # only rewrite if there is a chat history
    def route(info):
        if len(info["chat_history"])!=0:
            return rewriting
        else:
            return no_rewriting

    # RunnableParallel so all value "chains" of the dictionary get parallel excecuted, itemgetter to get the values of keys from the input dictionary
    retrieval = RunnableParallel({"context": itemgetter("input") | retriever | format_docs, "input": itemgetter("input"), "chat_history": itemgetter("chat_history")})

    # rewrite the query given the chat history
    rewriting = RunnableParallel({"input": rewriting_prompt | model | parser, "chat_history": itemgetter("chat_history")})

    # do nothing and pass on the entire dictionary 
    no_rewriting = RunnablePassthrough()

    # rag chain that gets invoked when you generate a response
    rag_chain = route | retrieval | rag_prompt | model | parser
    return rag_chain

def generate_response(message: str, history_list: list[dict], stream: bool = True) -> str | Generator[str, None, None]:
    chat_history =[]
    # transform the chat history to langchain chat_history list
    # wrapping messages with Human and AIMessage
    for mes in history_list:
        m = HumanMessage(content=mes["content"]) if mes["role"] == "user" else AIMessage(content=mes["content"])
        chat_history.append(m)   
    chain = initialize_chain()     
    # option to choose streaming or getting the entire response at once
    if stream:
        response = chain.stream({
            "chat_history": chat_history, 
            "input": message
            })
    else: 
        response = chain.invoke({
            "chat_history": chat_history, 
            "input": message
            })       
    return response
     

def get_relevant_sources() -> list[tuple[str, str]]:
    confluence_spacekey=os.getenv("LANGCHAIN_API_KEY")
    relevant_sources = []
    # relevant_docs are the chunks, each wrapped in a Document object
    for doc in relevant_docs:
        # put together the link to page of the chunk
        source = f"https://llmgruppenarbeit.atlassian.net/wiki/spaces/{confluence_spacekey}/pages/" + doc.metadata.get("p_id")
        title = doc.metadata.get("p_title")
        # tuple of the title and link of the chunk
        new_dic = (title, source)
        # no duplicates allowed
        if new_dic not in relevant_sources:
            relevant_sources.append(new_dic) 
    return relevant_sources
        

# send feedback (1 or 0) to tracer langsmith 
def send_feedback(run_id: str, score: Literal[0,1]):
    key = f"user_score_{run_id}"
    client.create_feedback(run_id, key=key, score=score)




# for debugging purposes
if __name__ == "__main__":
    chat_history = []
    
    while True:
        user_input = input("You: ")
        if user_input.lower() == 'exit':
            break
        response = generate_response(user_input, chat_history, stream=False)
        chat_history.append({"role": "user", "content": user_input})
        chat_history.append({"role": "assistant", "content": response})
        print(response)







# Chroma DB
#CHROMA_PATH = "chroma"


# def get_chunks_from_chroma(message):
#     # Prepare the DB.
#     db = Chroma(persist_directory=CHROMA_PATH, embedding_function=embeddings)

#     # Search the DB for relevant chunks for answering the question
#     results = db.similarity_search_with_relevance_scores(message, k=MAX_NUMBER_OF_RESULTS)
    
#     # Filter relevant chunks to only the chunks with a simalarity greater than treshhold 
#     filtered_results = [(doc, score) for doc, score in results if score >= THRESHHOLD_SIMILARITY]

#     return filtered_results

         